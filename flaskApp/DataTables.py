from MySQLdb import cursors
from flask import request
from collections import defaultdict, namedtuple
import re

BOOLEAN_FIELDS = (
    "search.regex", "searchable", "orderable", "regex"
)


class DataTablesServer(object):
 
    def __init__( self, request, index, table, db):
        print("Init datatables")
        
        self.index = index
        self.table = table
        # values specified by the datatable for filtering, sorting, paging
        self.req_data = request.get_json(force=True)
        self.columns = self.req_data['columns']

        print(self.columns)
        
        self.column_names = [col['name'] for col in self.columns]

        # pass MysqlDB cursor
        self.db = db
 
        # results from the db
        self.resultData = None
         
        # total in the table after filtering
        self.cadinalityFiltered = 0
 
        # total in the table unfiltered
        self.cadinality = 0
        
        self.run_queries()


 
    def output_result(self):
        # return output
        output = {}
        output['draw'] = str(int(self.req_data['draw']))
        output['recordsTotal'] = str(self.cardinality)
        output['recordsFiltered'] = str(self.cadinalityFiltered)
        data_rows = []
 
        for row in self.resultData:
            data_row = []
            for i in range( len(self.columns) ):
                data_row.append(str(row[ self.column_names[i] ]).replace('"','\\"'))
             
            # add additional rows here that are not represented in the database
            # data_row.append(('''<input id='%s' type='checkbox'></input>''' % (str(row[ self.index ]))).replace('\\', ''))
 
            data_rows.append(data_row)
 
        output['data'] = data_rows 
        return output
 
    def run_queries(self):
        dataCursor = self.db.cursor(cursors.DictCursor) # replace the standard cursor with a dictionary cursor only for this query
        query = """
            SELECT SQL_CALC_FOUND_ROWS {columns}
            FROM   {table} {where} {group} {order} {limit}""" .format(
                columns=self.columns_aggregate(), table=self.table,
                where=self.filtering(), order=self.ordering(), 
                group=self.grouping(), limit=self.paging() 
            )
        print(query)
        dataCursor.execute(query)
        self.resultData = dataCursor.fetchall()

        cadinalityFilteredCursor = self.db.cursor()
        cadinalityFilteredCursor.execute( """
            SELECT FOUND_ROWS()
        """ )
        self.cadinalityFiltered = cadinalityFilteredCursor.fetchone()[0]

        cadinalityCursor = self.db.cursor()
        cadinalityCursor.execute( """SELECT COUNT(%s) FROM %s""" % (self.index, self.table))
        self.cardinality = cadinalityCursor.fetchone()[0]

    # Aggregates across columns that are visible and searchable.
    # Defines weighted average and response count functions
    # Sets all hidden columns to report "" values
    def columns_aggregate(self):
        column_strs=[]
        last_col_visible = False
        for col in self.columns:
            if (not col['visible']                                      #column not visible
                and not (col['name'][-2:]=='_N' and last_col_visible)):   #but not a hidden _N column with preceding col visible
                # if the column is not visible, null it out to avoid calculation
                column_strs.append('"" AS {}'.format(col['name'])) 
            elif not col['searchable']:
                # if the column isn't searchable, it's an output column: do aggregation
                if col['name'][-2:] == '_N': # sample count column
                    column_strs.append("SUM({samples}) AS {samples}".format(samples=col['name']))
                elif col['name']+'_N' in self.column_names: # value average column
                    column_strs.append("SUM({avg}*{samples})/SUM({samples}) AS {avg}"
                                        .format(avg=col['name'],samples=col['name']+'_N'))
            else:
                # if the column is visible and searchable, it's a parameter column
                column_strs.append(col['name'])

            last_col_visible = col['visible']

        return ','.join(column_strs)

 
    def filtering(self):
        filter = ""
        if ( self.req_data.has_key('search') ) and ( self.req_data['search']['value'] != "" ):
            filters = []
            for i in range( len(self.columns) ):
                filters.append("{} LIKE '%{}%'".format(self.column_names[i], self.req_data['search']['value']))
            filter = "WHERE " + " OR ".join(filters)

        return filter

        
        # individual column filtering if needed
        
        #and_filter_individual_columns = []
        #for i in range(len(columns)):
        #    if (request_values.has_key('sSearch_%d' % i) and request_values['sSearch_%d' % i] != ''):
        #        individual_column_filter = {}
        #        individual_column_filter[columns[i]] = {'$regex': request_values['sSearch_%d' % i], '$options': 'i'}
        #        and_filter_individual_columns.append(individual_column_filter)
 
        #if and_filter_individual_columns:
        #    filter['$and'] = and_filter_individual_columns
        #return filter

    def ordering( self ):
        order_str = ""
        if self.req_data['order'][0]['column'] != "":
            orders = []
            for order in self.req_data['order']:
                # Validate input
                col_i = int(order['column'])
                order_dir = order['dir'] if order['dir'] in ['asc','desc'] else None

                orders.append("{} {}".format(self.column_names[col_i], order_dir))

            order_str = "ORDER BY " + ",".join(orders)
           
        return order_str

    def grouping(self):
        group_str = ""
        #if we're passing visiblity info
        if self.columns[0].has_key('visible'):
            groups = [col['name'] for col in self.columns 
                        if col['visible']==True and # Group by visible param cols
                           col['searchable']==True] # Searchable, i.e. param columns
            group_str = "GROUP BY " + ",".join(groups) if groups else ""

        return group_str
 
    def paging(self):
        limit_str = ""
        if ( self.req_data['start'] != "" ) and ( self.req_data['length'] != -1 ):
            limit_str = "LIMIT {:d}, {:d}".format(self.req_data['start'], self.req_data['length'] )
        return limit_str

