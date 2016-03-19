from __future__ import print_function
from eval_db import Database



with open("questions.csv","w") as f:
	db = Database()

	db.cur.execute("SELECT * FROM Questions")
	qs = db.cur.fetchall()

	ShortStrings = ['CourseQuality', 'ProfQuality', 'TextbookValue', 'HomeworkValue', 'CourseOrganization', 'ProfCommunicationClarity', 'ProfUnderstandableExplanations', 'ProfSpeakingClarity', 'AmountLearned', 'CourseChallenge', 'ProfInterest', 'CourseStimulatedInterest', 'ProfEncouragedCommunication', 'Workload', 'AmountParticipation', 'AmountEffort', 'ProfPrepared', 'ProfEffectiveUseOfTime', 'ProfEncouragedQuestions', 'ProfRespect', 'HelpfulFeedback', 'EffectiveExams', 'FairGrades', 'Grade', 'CourseRole', 'TotalHours', 'ProfShowedLabUsage', 'LabCondition', 'GoodLabProcedures', 'LabChallenge', 'LabClarity', 'TotalHoursInClass', 'TotalHoursOutOfClass']

	print("Num,ShortString,FullString",file=f)

	for idx,q in enumerate(qs):
		print('"{Num}","{ShortStrings}","{FullString}"'.format(ShortStrings=ShortStrings[idx],**q),file=f)



