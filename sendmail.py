import smtplib

class Sendmail(object):

    def __init__(self):
        self.smtp = smtplib.SMTP('content-analysis.org', 587, 'amcat.fsw.vu.nl')
        self.smtp.login('helpdesk@contentanalysis.nl','knzhrm!!')

    def sendmail(self, addressee, subject, msg):
        msg = "Subject: %s\n\n%s" % (subject, msg)
        self.smtp.sendmail('helpdesk@contentanalysis.nl', addressee, msg)

    def quit(self):
        self.smtp.quit()

def sendmail(addressee, subject, msg):
    s = Sendmail()
    s.sendmail(addressee, subject, msg)
    s.quit()


if __name__ == '__main__':
    
    msg = '''Dit is een test'''
    sendmail('wouter@2at.nl', 'nog een testmail', msg)

