#!/usr/bin/python

import MySQLdb as mdb
import ConfigParser

#!! Check commit places

class Database(object):
    def __init__(self, **kwargs):
        # Database info can be passed in as arguments, or read from cfg file
        if kwargs:
            self.con = mdb.connect(**kwargs)
        else:
            cfg = ConfigParser.ConfigParser()
            cfg.read('database_local.cfg')
            opt = cfg._sections['MySQL database']

            self.con = mdb.connect(host = opt['hostname'],
                                   db = opt['database'],
                                   user = opt['username'],
                                   passwd = opt['password'])
            
        self.cur = self.con.cursor(mdb.cursors.DictCursor)

    def store(self, obj):
        obj.store(self.cur)
        self.con.commit()

    def get_col_grouped_by(self, class_, col, groups):
        # Increase GROUP_CONCAT limit
        self.cur.execute("SET @@session.group_concat_max_len = @@global.max_allowed_packet")

        template = "SELECT {groups},GROUP_CONCAT({col}) AS {col} FROM {table} GROUP BY {groups}"
        self.cur.execute(template.format(col=col,
                                         groups = ",".join(groups),
                                         table = class_.table))
        grouped_cols = self.cur.fetchall()
        # Separate grouped crn strings into list of crns
        for row in grouped_cols:
            row[col] = row[col].split(",")
        return grouped_cols

    def clear_table(self, class_):
        self.cur.execute("TRUNCATE TABLE {}".format(class_.table))

    def reset(self):
        #Clean database
        self.cur.execute("DROP TABLE IF EXISTS AnswerFields,Classes,Terms,Questions,FailedScrapes")
        self.cur.execute("DROP VIEW IF EXISTS QuestionAvgs")

        self.cur.execute("""CREATE TABLE Terms( 
                        TermID          INT PRIMARY KEY AUTO_INCREMENT,
                        Name            VARCHAR(10) UNIQUE,
                        Month           FLOAT(3,1))""")
                        #Month - Time, in months, from Jan 1st to middle of term.
                        #           i.e. Mid December = -0.5, Mid March is 2.5

        self.cur.executemany("INSERT INTO Terms (Name,Month) VALUES (%s,%s)",
                        [('A',      -3.5),
                         ('Fall',   -2.5),
                         ('B',      -1),
                         ('C',      1.2),
                         ('Spring', 2.3),
                         ('D',      3),
                         ('E1',     5.3),
                         ('E2',     7),
                         ('Summer', 6.2)])

        self.cur.execute("""CREATE TABLE Classes( 
                        ClassID        INT PRIMARY KEY AUTO_INCREMENT, 
                        CRN             INT, 
                        AcademicYear    YEAR, 
                        TermID            INT, 
                        FOREIGN KEY (TermID) REFERENCES Terms(TermID), 
                        Name            VARCHAR(40), 
                        Department      VARCHAR(4), 
                        Code            VARCHAR(4), 
                        Section         VARCHAR(4), 
                        Instructor      VARCHAR(100), 
                        ADLN            BOOL, 
                        CONSTRAINT uc_Class UNIQUE (CRN,AcademicYear,Instructor))""")
                        #AcademicYear- Year that started Jan or this course's academic year
                        #ADLN- Advanced Distance Learning Network course

        self.cur.execute("""CREATE TABLE Questions( 
                        QuestionID      INT PRIMARY KEY AUTO_INCREMENT, 
                        Num             VARCHAR(3),
                        FullString      VARCHAR(500),
                        ShortString     VARCHAR(50),
                        CONSTRAINT uc_Question UNIQUE (Num,FullString))""")#!!

        with open('questions.cvs') as f:
            self.load_questions_from_file(f)

        self.cur.execute("""CREATE TABLE AnswerFields( 
                        AnswerFieldID   INT PRIMARY KEY AUTO_INCREMENT, 
                        AnswerText      VARCHAR(50),
                        Weight          INT,
                        Respondents     INT, 
                        QuestionID      INT, 
                        FOREIGN KEY (QuestionID) REFERENCES Questions(QuestionID), 
                        ClassID         INT,                     
                        FOREIGN KEY (ClassID) REFERENCES Classes(ClassID),
                        CONSTRAINT uc_AnswerField UNIQUE (QuestionID,ClassID,AnswerText,Weight))""")

        # Record failed downloads/parses so we can rerun them
        self.cur.execute("""CREATE TABLE FailedScrapes(
                        FailedScrapeID  INT PRIMARY KEY AUTO_INCREMENT,
                        CRN             INT,
                        AcademicYear    YEAR,
                        ADLN            BOOL)""")#!! unique

        # Create view for average question answers, which is usually what we care about
        self.cur.execute("""CREATE VIEW QuestionAvgs AS
                        SELECT 
                            ClassID, 
                            QuestionID, 
                            SUM(Weight*Respondents)/SUM(Respondents) AS Avg,
                            SUM(Respondents) AS Respondents
                        FROM 
                            AnswerFields 
                        GROUP BY 
                            ClassID,
                            QuestionID""")

        self.build_class_summaries()

    # Load questions table from csv file #!!
    def load_questions_from_file(self, f):
        lines = f.read().splitlines()
        headers = lines[0]
        for line in lines[1:]:
            self.cur.execute("INSERT INTO Questions ({cols}) VALUES ({vals})"
                                .format(cols=headers,vals=line))

    # Load answer weights and assign them to all existing AnswerFields
    def load_answer_weights_from_file(self,f):
        lines = f.read.splitlines()
        if lines[0] != "AnswerText,Weight":
            raise Exception('First line of answer weight file must be AnswerText,Weight') #!! replace with better exception

        for line in lines[1:]:
            self.cur.execute("UPDATE Weight={1} FROM AnswerFields WHERE AnswerText='{0}'".format(*line.split(",")))#!! wrong, fix

    # Build view with all of the columns DataTables might request
    def build_class_summaries(self):
        # Get questions
        self.cur.execute("SELECT ShortString FROM Questions")
        short_strings = [row['ShortString'] for row in self.cur.fetchall()]
        # Create view for question responses per course
        
        rows_to_cols = ["""MAX(IF(ShortString='{string}',Avg,'')) AS {string},
                           MAX(IF(ShortString='{string}',Respondents,'')) AS {string}_N""".format(string=short_string) for short_string in short_strings]
        self.cur.execute("DROP TABLE IF EXISTS ClassSummaries")
        self.cur.execute("""CREATE TABLE ClassSummaries AS
                        SELECT                            
                            AcademicYear,
                            Terms.Name AS Term,
                            Department,
                            CONCAT(Department,'-',Code) AS Course,
                            Classes.Name,
                            Section,
                            CRN,
                            Instructor,
                            ADLN,
                            {}
                        FROM
                            Classes
                                JOIN
                            Terms ON Terms.TermID = Classes.TermID
                                JOIN
                            QuestionAvgs ON QuestionAvgs.ClassID = Classes.ClassID
                                JOIN
                            Questions ON Questions.QuestionID = QuestionAvgs.QuestionID
                        GROUP BY
                            Classes.ClassID
                        """.format(",".join(rows_to_cols)))

class Storable(object):
    field_blacklist = []
    table = None
    id_col = None

    def __init__(self, **kwargs):
        self.__dict__ = kwargs

    def execute_template(self, cur, query_template):
        db_pairs = [(key,val) for key,val in self.__dict__.items() 
                        if key not in self.field_blacklist]
        db_cols, db_vals = zip(*db_pairs)
        
        where_clause = " AND ".join(col+"=%s" for col in db_cols)
        query = query_template.format(table = self.table,
                                      cols = ",".join(db_cols),
                                      vals = ",".join(['%s']*len(db_vals)),
                                      where_clause = where_clause,
                                      id_col = self.id_col)

        cur.execute(query,db_vals)
        return cur.fetchone()

    def store(self, cur):
        self.execute_template(cur,"INSERT INTO {table} ({cols}) VALUES ({vals})")

        cur.execute("SELECT LAST_INSERT_ID()")
        row_id = cur.fetchone()['LAST_INSERT_ID()']

        return row_id

    def get_ID(self, cur):
        result = self.execute_template(cur,"SELECT {id_col} FROM {table} WHERE {where_clause}")

        if result:
            return result[self.id_col]
        else:
            return None
            
class Evaluation(Storable):
    field_blacklist = ["questions","TermName"]
    table = "Classes"
    id_col = "ClassID"

    def __init__(self, **kwargs):
        self.__dict__ = kwargs
        self.questions = []        

    def store(self, cur):
        #replace TermName with TermID
        try:
            cur.execute("SELECT TermID FROM Terms WHERE Name=%s",self.TermName)
            self.TermID = cur.fetchone()['TermID']
        except AttributeError: 
            #We don't have a TermName, so don't bother with term
            pass

        class_id = super(Evaluation, self).store(cur)

        for question in self.questions:
            question.ClassID = class_id
            question.store(cur)


class QuestionInstance(Storable):
    field_blacklist = ["answers", "ClassID"]
    table = "Questions"
    id_col = "QuestionID"

    def __init__(self, **kwargs):
        self.__dict__ = kwargs
        self.answers = []        

    def store(self, cur):
        q_id = self.get_ID(cur)

        # If the question wasn't found, store it
        if not q_id:
            q_id = super(QuestionInstance, self).store(cur)

        for answer in self.answers:
            answer.QuestionID = q_id
            answer.ClassID = self.ClassID
            answer.store(cur)

class AnswerField(Storable):
    table = "AnswerFields"
    id_col = "AnswerFieldID"

class FailedScrape(Storable):
    table = "FailedScrapes"
    id_col = "FailedScrapeID"





