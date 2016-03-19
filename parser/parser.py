#!/usr/bin/python

from __future__ import print_function
from eval_db import Database, Evaluation, QuestionInstance, AnswerField, FailedScrape
from bs4 import BeautifulSoup
from MySQLdb import DatabaseError
import re
import sys
import requests
import getpass
import traceback
import getopt

# Python 3 improvements: flush prints insted of using -u
# super lookes nicer
# print doesn't need future import
# better re-raise with raise from, tracebacks

# There can be two courses with same year and CRN (and name, code, section, etc), but different profs
#	(ex: 2015 ADLN, CRN=30071)
# Don't trust means on course selection page
#!! notes: dont go by prof, get all at once by not giving prof param
# There's a Summer term
# Sections can be 1 digit
# Which E term is hard to figure out. Usually E01 or E02, sometimes E04
# Use siblings to do the find_all, or just check IN_CRN for each href

#!! Add weights for text answers
# Use Postgre Materialized Views to avoid recomputing views

pages = {
    'home': 'https://bannerweb.wpi.edu',
    'login': 'https://bannerweb.wpi.edu/pls/prod/twbkwbis.P_WWWLogin',
    'validate': 'https://bannerweb.wpi.edu/pls/prod/twbkwbis.P_ValLogin',
    'sel_year_adln': 'https://bannerweb.wpi.edu/pls/prod/hwwkscevrp.P_Select_Year',
    'sel_course_inst': 'https://bannerweb.wpi.edu/pls/prod/hwwkscevrp.P_Select_CrseInst',
    'sel_section': 'https://bannerweb.wpi.edu/pls/prod/hwwkscevrp.P_Select_CrseSect'
}

# Allow reraising other exceptions as ParseErrors while preserving info
class ParseError(Exception):
    def __init__(self, message="", cause=None):
        cause_str = "caused by " + repr(cause) if cause else ""
        punc = ", " if message and cause else ""
        full_message = message + punc + cause_str

        if full_message:
            super(ParseError, self).__init__(full_message)
        
        self.cause = cause

class LoginError(Exception):
	pass

class Downloader(object):
	def __init__(self, user, passwd):
		self.user = user
		self.passwd = passwd
		self.sess = requests.Session()

	def login(self):
		print("\nLogging in... ", end='')
		self.sess.get(pages['home'])
		response = self.sess.post(pages['validate'], 
								params = {
									'sid': self.user,
									'PIN': self.passwd},
								headers = {
									'referer': pages['login']
								})

		# Check cookies for success
		if 'SESSID' not in self.sess.cookies:
			raise LoginError('Login unsuccessful')
		else:
			print("Success")

	def download_eval(self, url):
		if 'SESSID' not in self.sess.cookies:
			self.login()

		response = self.sess.get(pages['home'] + url, timeout=60)
		return response.text

	# Return list of urls for year, adln, and crn filters. crns is list of strings.
	def get_urls_by_year(self, year, adln=False, crns=[]):
		if 'SESSID' not in self.sess.cookies:
			self.login()

		print("Downloading list of {} classes for {}-{}... ".format(
				"ADLN" if adln else "non-ADLN", year-1,year), end='')

		response = self.sess.post(pages['sel_section'],
									params = {
										'IN_SUBCRSE': '',
										'IN_PIDM': '',
										'IN_ACYR': year,
										'IN_ADLN_OIX': "O" if adln else "X"
									}, timeout=600)
		print("Done")

		soup = BeautifulSoup(response.text, 'lxml')
		if crns:
			# Filter urls by provided list of crns
			regex_str = r".*IN_CRN=(?:" + "|".join(crns) + ")" 
		else:
			# If no list provided, get all crns
			regex_str = r".*IN_TYPE=C"

		class_links = (soup.find('table', class_='datadisplaytable')
				   	 .find_all('a', href=re.compile(regex_str)))

		# Return a list so we have its length available
		return [link['href'] for link in class_links]

def parse_eval(text):
    # Replace any &nbsp; in the html with spaces
    #text = unicode(text, 'utf-8')
    # Expects unicode text
    text = text.replace(u'\xa0',' ')

    soup = BeautifulSoup(text, "lxml")

    # Extract all class info from the header
    ev = Evaluation()
    try:
        ev.AcademicYear = re.compile(r"Academic Year \d{4}-(\d{4})").search(text).group(1)

        crn_re = re.compile(r"CRN (\d+)")
        crn_str = soup.find(string=crn_re)
        header_row = str(crn_str.parent.parent)

        ev.CRN = crn_re.search(crn_str).group(1)

        # Decompose parts of full course name
        course = re.compile(r"<b>([A-Z]{2,4})-([\dX]{3,4}) (.+?)</b>").search(header_row)
        ev.Department = course.group(1)
        ev.Code = course.group(2)
        ev.Name = course.group(3)

        # Identify term from section or season
        ev.Section = section = re.compile(r"Section (\w+)").search(header_row).group(1)
        if section[0] in 'ABCD':
            ev.TermName = section[0]
        #elif section[0:2] in ['E1','E2']:
        #    ev.TermName = section[0:2]
        else:
            ev.TermName = re.compile(r"(Spring|Fall|Summer) \d{4}").search(header_row).group(1)

        ev.Instructor = re.compile(r"<TH.*?>Prof\. (.*?)</TH>").search(text).group(1)

    except (AttributeError,IndexError) as e:
        raise ParseError("Error parsing course info",e), None, sys.exc_info()[2]

    # Extract all questions, possible answers, and answer values
    try:
        question_re = re.compile(r"(\d{1,2}[A-Z]?)\. (.+)")
        question_tags = soup.find_all("td", class_="dddefault", string=question_re)
        for q_tag in question_tags:
            # Pull out question number and text
            question_num, question_str = question_re.search(q_tag.string).groups()
            question = QuestionInstance(Num=question_num, FullString=question_str)

            # Pull out answer text (if available) and responses
            row_cells = q_tag.parent.find_all("td", class_="dddefault")        
            if len(row_cells)==8: # All data on one row, 1-5 response
                question.answers = [AnswerField(
                                        Weight=i+1, 
                                        Respondents=row_cells[i+1].p.contents[1])
                                    for i in range(5)]
            elif len(row_cells)==1: 
                # Answers are one per row on next 5 rows
                for row in q_tag.parent.next_siblings:
                    if row.name=="tr":
                        #Break when we get to the next question header
                        if row.find("th", class_="ddtitle"):
                            break
                        cells = row.find_all("td", class_="dddefault")
                        
                        answer = AnswerField(AnswerText=cells[0].string,
                                             Respondents=cells[1].string)

                        question.answers.append(answer)

            else:
                raise ParseError("Unexpected number of cells in row")

            ev.questions.append(question)

    except (AttributeError,IndexError) as e: 
        raise ParseError("Error parsing evaluation results",e), None, sys.exc_info()[2]

    return ev

# Only call options: 
# process_evals(db, dl, year, adln)
# process_evals(db, dl, year, adln, crns)
# process_evals(db, dl, urls)
def process_evals(db, dl, year=None, adln=False, crns=[], urls=[]):
	use_urls = bool(urls)
	if not urls:
		urls = dl.get_urls_by_year(year, adln, crns)

	for idx, url in enumerate(urls):
		crn = re.compile('IN_CRN=(\d*)').search(url).group(1)

		if use_urls: #extract year, adln from url if needed
			year = re.compile('IN_ACYR=(\d*)').search(url).group(1)
			adln_char = re.compile('IN_ADLN_OIX=(\d*)').search(url).group(1)
			if adln_char not in 'OX':
				raise ParseError("ADLN in URL must be O or X")
			adln = adln_char=='O'

		print("\rDownloading and parsing class {}/{} (CRN {})".format(idx+1,len(urls),crn), end='')

		try:
			eval_text = dl.download_eval(url)
			ev = parse_eval(eval_text)
			ev.ADLN = adln
			db.store(ev)
		except ParseError as e:
			#_, _, tb = sys.exc_info()
			print("\nError parsing CRN {}:".format(crn))
			traceback.print_exc()
			print()
			db.store(FailedScrape(CRN=crn, AcademicYear=year, ADLN=adln))
			#print(crn, year, adln, file=failed_crns)				
		except DatabaseError as e:
			print("\nError storing CRN {}: {}\n".format(crn, repr(e)))
			db.store(FailedScrape(CRN=crn, AcademicYear=year, ADLN=adln))
			#print(crn, file=failed_crns)
		except requests.exceptions.Timeout:
			print("\nTimed out while loading CRN {}".format(crn))
			db.store(FailedScrape(CRN=crn, AcademicYear=year, ADLN=adln)) #!!Add reason to failed table

	print("\nDone")

def process_failed_scrapes(db, dl):
	grouped_crns = db.get_col_grouped_by(FailedScrape,'CRN',['AcademicYear','ADLN'])
	db.clear_table(FailedScrape)
	
	for group in grouped_crns:
		process_evals(db, dl, group['AcademicYear'], group['ADLN'], group['CRN'])

#!!
# check all courses for uniqueness of crns
def validate_assumptions(db, dl):
	pass

# Check that all available courses are accounted for in db or failures
# Does not identify duplicate CRNS #!!
# untested #!!
def validate_and_fix(db, dl, fix=True):
	#years = dl.get_years() #!!!
	years = range(2006,2017)
	years = [2010,2013,2016] #!!! remove
	missing_urls = []

	for year in years:
		missing_in_year = []

		urls = dl.get_urls_by_year(year, adln=True)
		urls += dl.get_urls_by_year(year, adln=False)

		for url in urls:
			#!! get this from func
			crn = re.compile('IN_CRN=(\d*)').search(url).group(1) #!!
			adln_char = re.compile('IN_ADLN_OIX=(\d*)').search(url).group(1)
			if adln_char not in 'OX':
				raise ParseError("ADLN in URL must be O or X")
			adln = adln_char=='O'

			db.cur.execute("""SELECT 1 FROM ( 
								SELECT CRN FROM Classes WHERE AcademicYear = {year} AND ADLN={adln}
									UNION ALL
								SELECT CRN FROM FailedScrapes WHERE AcademicYear = {year} AND ADLN={adln}
									) a 
								WHERE CRN = {crn}""".format(year=year,adln=adln,crn=crn))
			if not db.cur.fetchone():
				# Entry not found
				missing_in_year.append(url)
		print("{} missing entries in {}-{} out of {}".format(len(missing_in_year),year-1,year,len(urls)))
		missing_urls += missing_in_year

	if missing_urls:
		print("{} total missing entries".format(len(missing_urls)))
		if fix:
			process_evals(db, dl, urls=missing_urls)
	else:
		print("No missing entries found") #!! notify about failed scrapes

def main(argv):
	try:
		opts, args = getopt.getopt(argv, "ry:fvb")
	except getopt.GetoptError:
		print("parser.py [-r] [-y YEAR | -f] [-v]") #!!
		return

	reset = False
	rerun_fails = False
	year = None
	validate = False
	build_sums = False
	for opt, arg in opts:
		if opt =='-r':
			reset = True
		elif opt =='-y':
			year = arg
		elif opt =='-f':
			rerun_fails = True
		elif opt == '-v':
			validate = True
		elif opt == '-b':
			build_sums = True

	db = Database()
	
	dl = Downloader(user=raw_input("Username: "),
					passwd=getpass.getpass())

	dl.login()

	if reset:
		db.reset()

	if rerun_fails:
		print("Re-running failed scrapes")
		process_failed_scrapes(db, dl)
	elif year:
		process_evals(db, dl, 2015, adln=True)
		process_evals(db, dl, 2015, adln=False)		
	elif validate:
		validate_and_fix(db, dl)
	elif build_sums:
		pass
	else:
		for year in range(2016,2017):
			process_evals(db, dl, year, adln=True)
			process_evals(db, dl, year, adln=False)		
		#!! process all years


	db.build_class_summaries()
    # with open("sampleeval.html") as f:
    #     ev = parse_eval(f.read())
    #     db.store(ev)
    # with open("sampleeval2.html") as f:
    #     ev = parse_eval(f.read())
    #     db.store(ev)

    # print("Complete")

if __name__ == "__main__":
	main(sys.argv[1:])
    


