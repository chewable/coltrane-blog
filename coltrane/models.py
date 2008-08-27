"""
Models for a weblog application.

"""


import datetime

from comment_utils.managers import CommentedObjectManager
from comment_utils.moderation import CommentModerator, moderator
from django.conf import settings
from django.db import models
from django.utils.encoding import smart_str
from django.utils.translation import ugettext_lazy as _
from django.core.exceptions import ImproperlyConfigured
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.comments import models as comment_models
import tagging
from tagging.fields import TagField
from template_utils.markup import formatter

from coltrane import managers

# Uses the optional COLTRANE_COMMENT_MODULE setting to load the appropriate
# comment model, falls back to django.contrib.comments
# Should be in the form <module>.<lowercase_modelname>
# e.g. COLTRANE_COMMENT_MODULE = 'threadedcomments.freethreadedcomment'
comment_module = getattr(settings, 'COLTRANE_COMMENT_MODULE', None)
try:
    app_label, model_name = comment_module.split('.')
    comment_model = models.get_model(app_label, model_name)
except (AttributeError, ImportError, ImproperlyConfigured):
    comment_model = settings.USE_FREE_COMMENTS and comment_models.FreeComment or comment_models.Comment

# Uses the optional COLTRANE_MODERATION_MODULE setting to determine the
# module that is used for moderation, usually inheriting from comment_utils.
# Should be in the form <python.path.to.module>
# e.g. COLTRANE_MODERATION_MODULE = 'threadedcomments.moderation'
moderation_module = getattr(settings, 'COLTRANE_MODERATION_MODULE', 'comment_utils.moderation')
try:
    mod = __import__(moderation_module, {}, {}, ['moderation'])
    moderator = getattr(mod, 'moderator')
    CommentModerator = getattr(mod, 'CommentModerator')
except ImportError:
    raise ImportError('Please check if you have set the COLTRANE_MODERATION_MODULE setting.')

class Category(models.Model):
    """
    A category that an Entry can belong to.
    
    """
    title = models.CharField(_('title'), max_length=250)
    slug = models.SlugField(_('slug'), unique=True, help_text=_('Used in the URL for the category. Must be unique.'))
    description = models.TextField(_('description'), help_text=_('A short description of the category, to be used in list pages.'))
    description_html = models.TextField(_('description HTML'), editable=False, blank=True)
    
    class Meta:
        verbose_name = _('category')
        verbose_name_plural = _('categories')
        ordering = ['title']
    
    def __unicode__(self):
        return self.title
    
    def save(self):
        self.description_html = formatter(self.description)
        super(Category, self).save()
    
    def get_absolute_url(self):
        return ('coltrane_category_detail', (), { 'slug': self.slug })
    get_absolute_url = models.permalink(get_absolute_url)
    
    def _get_live_entries(self):
        """
        Returns Entries in this Category with status of "live".
        
        Access this through the property ``live_entry_set``.
        
        """
        from coltrane.models import Entry
        return self.entry_set.filter(status__exact=Entry.LIVE_STATUS)
    
    live_entry_set = property(_get_live_entries)


class Entry(models.Model):
    """
    An entry in the weblog.
    
    Slightly denormalized, because it uses two fields each for the
    excerpt and the body: one for the actual text the user types in,
    and another to store the HTML version of the Entry (e.g., as
    generated by a text-to-HTML converter like Textile or Markdown).
    This saves having to run the conversion each time the Entry is
    displayed.
    
    Entries can be grouped by categories or by tags or both, or not
    grouped at all.
    
    """
    LIVE_STATUS = 1
    DRAFT_STATUS = 2
    HIDDEN_STATUS = 3
    STATUS_CHOICES = (
        (LIVE_STATUS, _('Live')),
        (DRAFT_STATUS, _('Draft')),
        (HIDDEN_STATUS, _('Hidden')),
        )
    
    # Metadata.
    author = models.ForeignKey(User, verbose_name=_('author'))
    enable_comments = models.BooleanField(_('enable comments'), default=True)
    featured = models.BooleanField(_('featured'), default=False)
    pub_date = models.DateTimeField(_('date posted'), default=datetime.datetime.today)
    slug = models.SlugField(_('slug'), unique_for_date='pub_date', max_length=100,
                            help_text=_('Used in the URL of the entry. Must be unique for the publication date of the entry.'))
    status = models.IntegerField(_('status'), choices=STATUS_CHOICES, default=LIVE_STATUS,
                                 help_text=_('Only entries with "live" status will be displayed publicly.'))
    title = models.CharField(_('title'), max_length=250)
    
    # The actual entry bits.
    body = models.TextField(_('body'))
    body_html = models.TextField(_('body HTML'), editable=False, blank=True)
    excerpt = models.TextField(_('excerpt'), blank=True, null=True)
    excerpt_html = models.TextField(_('excerpt HTML'), blank=True, null=True, editable=False)
    
    # Categorization.
    categories = models.ManyToManyField(Category, blank=True, verbose_name=_('categories'))
    tags = TagField()
    
    # Managers.
    objects = models.Manager()
    live = managers.LiveEntryManager()
    
    class Meta:
        get_latest_by = 'pub_date'
        ordering = ['-pub_date']
        verbose_name = _('entry')
        verbose_name_plural = _('entries')
    
    def __unicode__(self):
        return self.title
    
    def save(self):
        if self.excerpt:
            self.excerpt_html = formatter(self.excerpt)
        self.body_html = formatter(self.body)
        super(Entry, self).save()
        
    def get_absolute_url(self):
        return ('coltrane_entry_detail', (), { 'year': self.pub_date.strftime('%Y'),
                                               'month': self.pub_date.strftime('%b').lower(),
                                               'day': self.pub_date.strftime('%d'),
                                               'slug': self.slug })
    get_absolute_url = models.permalink(get_absolute_url)
    
    def _next_previous_helper(self, direction):
        return getattr(self, 'get_%s_by_pub_date' % direction)(status__exact=self.LIVE_STATUS)
    
    def get_next(self):
        """
        Returns the next Entry with "live" status by ``pub_date``, if
        there is one, or ``None`` if there isn't.
        
        In public-facing templates, use this method instead of
        ``get_next_by_pub_date``, because ``get_next_by_pub_date``
        does not differentiate entry status.
        
        """
        return self._next_previous_helper('next')
    
    def get_previous(self):
        """
        Returns the previous Entry with "live" status by ``pub_date``,
        if there is one, or ``None`` if there isn't.
        
        In public-facing templates, use this method instead of
        ``get_previous_by_pub_date``, because
        ``get_previous_by_pub_date`` does not differentiate entry
        status..
        
        """
        return self._next_previous_helper('previous')

    def _get_comment_count(self):
        model = comment_model
        ctype = ContentType.objects.get_for_model(self)
        return model.objects.filter(content_type__pk=ctype.id, object_id__exact=self.id).count()
    _get_comment_count.short_description = _('number of comments')

    def _get_category_count(self):
        return self.categories.count()
    _get_category_count.short_description = _('number of categories')


class Link(models.Model):
    """
    A link posted to the weblog.
    
    Denormalized in the same fashion as the Entry model, in order to
    allow text-to-HTML conversion to be performed on the
    ``description`` field.
    
    """
    # Metadata.
    enable_comments = models.BooleanField(_('enable comments'), default=True)
    post_elsewhere = models.BooleanField(_('post to del.icio.us'),
                                         default=settings.DEFAULT_EXTERNAL_LINK_POST,
                                         help_text=_('If checked, this link will be posted both to your weblog and to your del.icio.us account.'))
    posted_by = models.ForeignKey(User, verbose_name=_('posted by'))
    pub_date = models.DateTimeField(_('date posted'), default=datetime.datetime.today)
    slug = models.SlugField(_('slug'), unique_for_date='pub_date',
                            help_text=_('Must be unique for the publication date.'))
    title = models.CharField(_('title'), max_length=250)
    
    # The actual link bits.
    description = models.TextField(_('description'), blank=True, null=True)
    description_html = models.TextField(_('description HTML'), editable=False, blank=True, null=True)
    via_name = models.CharField(_('via'), max_length=250, blank=True, null=True,
                                help_text=_('The name of the person whose site you spotted the link on. Optional.'))
    via_url = models.URLField(_('via URL'), verify_exists=False, blank=True, null=True,
                              help_text=_('The URL of the site where you spotted the link. Optional.'))
    tags = TagField()
    url = models.URLField(_('URL'), unique=True, verify_exists=False)
    
    objects = CommentedObjectManager()
    
    class Meta:
        get_latest_by = 'pub_date'
        ordering = ['-pub_date']
        verbose_name = _('link')
        verbose_name_plural = _('links')
    
    def __unicode__(self):
        return self.title
    
    def save(self):
        if not self.id and self.post_elsewhere:
            import pydelicious
            try:
                pydelicious.add(settings.DELICIOUS_USER, settings.DELICIOUS_PASSWORD, smart_str(self.url), smart_str(self.title), smart_str(self.tags))
            except:
                pass # TODO: don't just silently quash a bad del.icio.us post
        if self.description:
            self.description_html = formatter(self.description)
        super(Link, self).save()
    
    def get_absolute_url(self):
        return ('coltrane_link_detail', (), { 'year': self.pub_date.strftime('%Y'),
                                              'month': self.pub_date.strftime('%b').lower(),
                                              'day': self.pub_date.strftime('%d'),
                                              'slug': self.slug })
    get_absolute_url = models.permalink(get_absolute_url)


class ColtraneModerator(CommentModerator):
    akismet = True
    auto_close_field = 'pub_date'
    email_notification = True
    enable_field = 'enable_comments'
    close_after = settings.COMMENTS_MODERATE_AFTER

moderator.register([Entry, Link], ColtraneModerator)

tagging.register(Entry, 'tag_set')
tagging.register(Link, 'tag_set')
