from flask import Flask, render_template, request
from DataTables import DataTablesServer
import json
import ConfigParser
from os import path

from flaskext.mysql import MySQL
from MySQLdb import cursors

app = Flask(__name__)

# Read in MySQL login info from file
cfg = ConfigParser.ConfigParser()
cfg.read(path.join(path.dirname(__file__), 'database.cfg'))
opt = cfg._sections['MySQL database']

app.config['MYSQL_DATABASE_HOST'] = opt['hostname']
app.config['MYSQL_DATABASE_USER'] = opt['username']
app.config['MYSQL_DATABASE_PASSWORD'] = opt['password']
app.config['MYSQL_DATABASE_DB'] = opt['database']

mysql = MySQL()
mysql.init_app(app)

# Columns visible on start
visible_cols = ["AcademicYear","Instructor","Course","CourseQuality"]

@app.route('/hello_world')
def hello_world():
        return 'Hello World!'

@app.route('/')
def main():
    db = mysql.get_db()
    cursor = db.cursor(cursors.DictCursor)
    cursor.execute("DESCRIBE ClassSummaries")
    col_names = [entry['Field'] for entry in cursor.fetchall()]
        
    sample_col_idxs = [idx for idx,name in enumerate(col_names) if name[-2:] == '_N']
    avg_col_idxs = [idx for idx,name in enumerate(col_names) if name+'_N' in col_names]
    param_col_idxs = [idx for idx in range(len(col_names)) if idx not in sample_col_idxs+avg_col_idxs]
    init_order = [[avg_col_idxs[0], 'desc']]    
    columns = [{"title": name, 
                "name": name, 
                # param cols are searchable, value cols aren't
                "searchable": idx not in sample_col_idxs+avg_col_idxs,
                "visible": name in visible_cols}
                    for idx,name in enumerate(col_names)]
    
    return render_template('index.html',columns = json.dumps(columns),
                                        sample_col_idxs = json.dumps(sample_col_idxs),
                                        avg_col_idxs = json.dumps(avg_col_idxs),
                                        param_col_idxs = json.dumps(param_col_idxs),
                                        init_order = json.dumps(init_order))

@app.route("/server_data", methods=['POST'])
def get_server_data():
    print("Start")

    data = request.get_json(force=True)
    print("Data:")

    index_column = "CRN" #!!
    table = "ClassSummaries"

    db = mysql.get_db()
    cursor = db.cursor(cursors.DictCursor)

    results = DataTablesServer(request, index_column, table, db).output_result()

    # return the results as json # import json
    print("Done")
    return json.dumps(results)

# Predict metrics for a list of CRNS, using knowlege about past classes
#!! untested
def predict_for_crns(crns, out_cols):
    db = mysql.get_db()
    cur = db.cursor(cursors.DictCursor)

    data = []
    for crn in crns:
        data.append({'crn':crn})

        # Try to get info about the specific class queried
        cur.execute("SELECT Department,Code,Name,Professor FROM Classes WHERE CRN=%s",crn)
        class_info = cur.fetchone()
        if not class_info: # CRN not in the database, we're done
            data[-1]['method'] = None
            continue

        data[-1].update(class_info)

        summaries = ["SUM({avg}*{samples})/SUM({samples} AS {avg}, SUM({samples}) AS {samples}"
                                    .format(avg=col, samples=col+"_N") for col in out_cols]
        cur.execute("""SELECT {summaries} FROM ClassSummaries WHERE 
                                            Course={Department}-{Code},
                                            Name={Name},
                                            Professor={Professor}
                                        GROUP BY
                                            Course,Name,Professor"""
                                            .format(summaries=summaries,**class_info))
        result = cur.fetchone()
        if result:
            data[-1].update(result)
            data[-1]['method'] = 'perfect'
            continue #we're done

        # If we couldn't find any matches, ignore the course name
        cur.execute("""SELECT {summaries} FROM ClassSummaries WHERE 
                                    Course={Department}-{Code},
                                    Professor={Professor}
                                GROUP BY
                                    Course,Professor"""
                                    .format(summaries=summaries,**class_info))
        result = cur.fetchone()
        if result:
            data[-1].update(result)
            data[-1]['method'] = 'courseAndProf'
            continue 

        # If we still failed for the same prof and same course, look at only one or the other
        cur.execute("""SELECT {summaries} FROM ClassSummaries WHERE 
                            Professor={Professor}
                        GROUP BY
                            Professor"""
                            .format(summaries=summaries,**class_info))
        result_prof = cur.fetchone()
        cur.execute("""SELECT {summaries} FROM ClassSummaries WHERE 
                                    Course={Department}-{Code},
                                GROUP BY
                                    Course"""
                                    .format(summaries=summaries,**class_info))
        result_course = cur.fetchone()
        if result_prof and result_course:
            #!! avg somehow, for now just use course
            data[-1].update(result_course)
            data[-1]['method'] = 'courseOrProf'
        elif result_course:
            data[-1].update(result_course)
            data[-1]['method'] = 'course'
        elif result_prof:
            data[-1].update(result_prof)
            data[-1]['method'] = 'prof'
        else: 
            data[-1]['method'] = 'none'

    return data



if __name__ == '__main__':
        app.run(debug=True)