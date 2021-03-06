###########################################################################
#          (C) Vrije Universiteit, Amsterdam (the Netherlands)            #
#                                                                         #
# This file is part of AmCAT - The Amsterdam Content Analysis Toolkit     #
#                                                                         #
# AmCAT is free software: you can redistribute it and/or modify it under  #
# the terms of the GNU Affero General Public License as published by the  #
# Free Software Foundation, either version 3 of the License, or (at your  #
# option) any later version.                                              #
#                                                                         #
# AmCAT is distributed in the hope that it will be useful, but WITHOUT    #
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or   #
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public     #
# License for more details.                                               #
#                                                                         #
# You should have received a copy of the GNU Affero General Public        #
# License along with AmCAT.  If not, see <http://www.gnu.org/licenses/>.  #
###########################################################################


"""
A Scraper is an object that knows how to scrape a certain resource. A scraper
is called by the controller,
"""

from django import forms
from django.forms.widgets import HiddenInput
from django.core.exceptions import ObjectDoesNotExist

from httplib2 import iri2uri

from amcat.scripts.script import Script
from amcat.models.article import Article
from amcat.models.project import Project
from amcat.models.medium import Medium
from amcat.models.articleset import ArticleSet, create_new_articleset

from amcat.scraping.htmltools import HTTPOpener
from amcat.scraping.document import Document

import logging; log = logging.getLogger(__name__)

class ScraperForm(forms.Form):
    """Form for scrapers"""
    project = forms.ModelChoiceField(queryset=Project.objects.all())

    articleset = forms.ModelChoiceField(queryset=ArticleSet.objects.all(), required=False, help_text="If you choose an existing articleset, the articles will be appended to that set. If you leave this empty, a new articleset will be created using either the name given below, or using the file name")
    articleset_name = forms.CharField(
        max_length=ArticleSet._meta.get_field_by_name('name')[0].max_length,
        required = False,
)

    def clean_articleset_name(self):
        name = self.cleaned_data['articleset_name']
        if not bool(name) ^ bool(self.cleaned_data['articleset']):
            raise forms.ValidationError("Please specify either articleset or articleset_name")
        return name
    
    @classmethod
    def get_empty(cls, project=None, post=None, files=None, **_options):
        f = cls(post, files) if post is not None else cls()
        if project:
            f.fields['project'].initial = project.id
            f.fields['project'].widget = HiddenInput()

            f.fields['articleset'].queryset = ArticleSet.objects.filter(project=project)
        return f

class Scraper(Script):
    output_type = Article
    options_form = ScraperForm

    # if non-None, Medium on articles will be automatically set to a
    # medium with this name (which will be created if necessary)
    medium_name = None

    def __init__(self, *args, **kargs):
        super(Scraper, self).__init__(*args, **kargs)
        self.medium = Medium.get_or_create(self.medium_name)
        self.project = self.options['project']
        for k, v in self.options.items():
            if type(v) == str:
                self.options[k] = v.decode('utf-8')

        # avoid django problem/bug with repr(File(open(uncode-string)))
        # https://code.djangoproject.com/ticket/8156   
        o2 = {k:v for k,v in self.options.iteritems() if k != 'file'}
        log.debug(u"Articleset: {self.articleset!r}, options: {o2}"
                  .format(**locals()))
    @property
    def articleset(self):
        if self.options['articleset']:
            return self.options['articleset']
        if self.options['articleset_name']:
            aset = create_new_articleset(self.options['articleset_name'], self.project)
            self.options['articleset'] = aset
            return aset
        return
        
    def run(self,input=None,deduplicate=False):
        log.info("Scraping {self.__class__.__name__} into {self.project}, medium {self.medium} using RobustController"
                 .format(**locals()))
        from amcat.scraping.controller import RobustController
        return RobustController(self.articleset).scrape([self],deduplicate)


    def get_units(self):
        """
        Split the scraping job into a number of 'units' that can be processed independently
        of each other.

        @return: a sequence of arbitrary objects to be passed to scrape_unit
        """
        self._initialize()        
        for unit in self._get_units():
            yield unit
            

    def _get_units(self):
        """
        'Protected' method to do the actual work of getting units. Will be called by get_units
        after running any needed initialization. By default, returns a single 'None' unit.
        Subclasses that override this method are encouraged to yield rather than return
        units to facilitate multithreaded

        @return: a sequence of arbitrary objects to be passed to scrape_unit
        """
        return [None]

    def scrape_unit(self, unit):
        """
        Scrape a single unit of work. Subclasses can override _scrape_unit to have project
        and medium filled in automatically.
        @return: a sequence of Article objects ready to .save()
        """
        articles = list(self._scrape_unit(unit))
        for article in self.process_in_order(articles):
            log.debug(unicode(".. yields article {article}".format(**locals()),'utf-8'))
            yield article


    def process_in_order(self, articles):
        """
        Perform article postprocessing in the right order, because parents have to be saved before children
        """
        #.parent is preferred over .props.parent
        for article in articles:
            if hasattr(article, 'props') and hasattr(article.props, 'parent'):
                article.parent = article.props.parent

        #initial list is any article without parent, or a non-existing parent
        toprocess = [a for a in articles if (not hasattr(a, 'parent')) or not a.parent in articles]

        for doc in toprocess:
            article = self._postprocess_article(doc)            
            #find children, add to toprocess
            for a in articles:
                if hasattr(a, 'props') and hasattr(a, 'parent') and a.parent == doc:
                    a.props.parent = article
                    toprocess.append(a)
                    
            yield article


    def _scrape_unit(self, unit):
        """
        'Protected' method to parse the articles from one unit. Subclasses should override
        this method (or the base scrape_unit). This method may be called from different threads
        so ensure thread-safe access to any globals or instance members.

        @return: an Article object or a Document object that
                 can be converted to an article. Either object can have the project
                 and medium properties unset as they will be set automatically if provided
        """
        raise NotImplementedError

    def _initialize(self):
        """
        Perform any intialization needed before starting the processing.
        """
        pass

    def _postprocess_article(self, article):
        """
        Finalize an article. This should convert the output of _scrape_unit to the required
        output for scrape_unit, e.g. convert to Article, add project and/or medium
        """
        comment = False
        if isinstance(article, Document):
            if hasattr(article, 'is_comment') and article.is_comment:
                if not hasattr(self, 'comment_medium'):
                    self.comment_medium = Medium.get_or_create(self.medium_name + " - Comments")
                comment = True
            article = article.create_article()

        if comment:
            _set_default(article, "medium", self.comment_medium)
        else:
            _set_default(article, "medium", self.medium)

        _set_default(article, "project", self.project)
        article.scraper = self
        return article


class AuthForm(ScraperForm):
    """
    Form for scrapers that require a login
    """
    username = forms.CharField()
    password = forms.CharField()

class ArchiveForm(ScraperForm):
    """
    Form for scrapers that scrape multiple dates
    """
    first_date = forms.DateField()
    last_date = forms.DateField()
    
class DateForm(ScraperForm):
    """
    Form for scrapers that operate on a date
    """
    date = forms.DateField()

class DatedScraper(Scraper):
    """Base class for scrapers that work for a certain date"""
    options_form = DateForm
    def _postprocess_article(self, article):
        article = super(DatedScraper, self)._postprocess_article(article)
        _set_default(article, "date", self.options['date'])
        return article
    def __unicode__(self):
        return "[%s for %s]" % (self.__class__.__name__, self.options['date'])

class DBScraperForm(DateForm,AuthForm):
    """
    Form for dated scrapers that need credentials
    """

class DBScraper(DatedScraper):
    """Base class for (dated) scrapers that require a login"""
    options_form = DBScraperForm

    def _login(self, username, password):
        """Login to the resource to scrape, if needed. Will be called
        at the start of get_units()"""
        pass

    def _initialize(self):
        self._login(self.options['username'], self.options['password'])

class HTTPScraper(Scraper):
    """Base class for scrapers that require an http opener"""
    def __init__(self, *args, **kargs):
        super(HTTPScraper, self).__init__(*args, **kargs)
        # TODO: this should be moved to _initialize, but then _initialize should
        # be moved to some sort of listener-structure as HTTPScraper is expected to
        # be inherited from besides eg DBScraper in a "diamon-shaped" multi-inheritance
        self.opener = HTTPOpener()
    def getdoc(self, url, encoding=None):
        try:
            return self.opener.getdoc(url, encoding)
        except UnicodeEncodeError:
            uri = iri2uri(url)
            return self.opener.getdoc(uri, encoding)

    def open(self, url,  encoding=None):
        if isinstance(url, (str, unicode)):
            if isinstance(url, unicode):
                url = url.encode('utf-8')
            log.info('Retrieving "{url}"'.format(**locals()))
            try:
                return self.opener.opener.open(url, encoding)
            except UnicodeEncodeError:
                uri = iri2uri(url)
                return self.opener.opener.open(uri, encoding)
        else:
            req = url
            log.info('Retrieving "{url}"'.format(url = req.get_full_url()))
            return self.opener.opener.open(req, encoding)
     


def _set_default(obj, attr, val):
    try:
        if getattr(obj, attr, None) is not None: return
    except ObjectDoesNotExist:
        pass # django throws DNE on x.y if y is not set and not nullable
    setattr(obj, attr, val)
   

if __name__ == '__main__':
    from amcat.scripts.tools import cli
    cli.run_cli(Scraper)
