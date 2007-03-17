"""
Models for a weblog application.

"""

import datetime, re
from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from tagging.models import Tag
import utils


ENTRY_STATUS_CHOICES = (
    (1, 'Live'),
    (2, 'Draft'),
    (3, 'Hidden'),
    )


class Category(models.Model):
    """
    A category that an Entry can belong to.
    
    """
    name = models.CharField(maxlength=250)
    slug = models.SlugField(prepopulate_from=('name',))
    description = models.TextField()
    
    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']
    
    class Admin:
        pass
    
    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        return "/weblog/categories/%s/" % self.slug


class EntryManager(models.Manager):
    """
    Custom manager for the Entry model, providing shortcuts for
    filtering by entry status.
    
    """
    def live(self):
        """
        Returns a QuerySet of Entries with "live" (published) status. 
        
        Useful for public views and especially for passing to generic
        views.
        
        """
        return self.filter(status__exact=1)
    
    def drafts(self):
        """
        Returns a QuerySet of Entries with "draft" (unpublished) status.
        
        Useful if you ever want to roll your own admin views for blog
        entries.
        
        """
        return self.filter(status__exact=2)
    
    def most_commented(self, num=5, free=True):
        """
        Returns the ``num`` Entries with the highest comment counts,
        in order.
        
        Pass ``free=False`` if you're using the registered comment
        model (Comment) instead of the anonymous comment model
        (FreeComment).
        
        """
        from django.db import connection
        from django.contrib.comments import models as comment_models
        from django.contrib.contenttypes.models import ContentType
        if free:
            comment_opts = comment_models.FreeComment._meta
        else:
            comment_opts = comment_models.Comment._meta
        ctype = ContentType.objects.get_for_model(self.model)
        query = """SELECT object_id, COUNT(*) AS score
        FROM %s
        WHERE content_type_id = %%s
        AND is_public = 1
        GROUP BY object_id
        ORDER BY score DESC""" % comment_opts.db_table
        
        cursor = connection.cursor()
        cursor.execute(query, [ctype.id])
        entry_ids = [row[0] for row in cursor.fetchall()[:num]]
        
        # Use ``in_bulk`` here instead of an ``id__in`` filter, because ``id__in``
        # would clobber the ordering.
        entry_dict = self.in_bulk(entry_ids)
        return [entry_dict[entry_id] for entry_id in entry_ids]


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
    # Metadata
    author = models.ForeignKey(User)
    enable_comments = models.BooleanField(default=True)
    pub_date = models.DateTimeField('Date posted', default=datetime.datetime.today)
    slug = models.SlugField(prepopulate_from=('title',))
    title = models.CharField(maxlength=250)
    status = models.IntegerField(choices=ENTRY_STATUS_CHOICES, default=1)
    
    # The actual entry bits.
    body = models.TextField()
    body_html = models.TextField(editable=False)
    excerpt = models.TextField(blank=True, null=True)
    excerpt_html = models.TextField(blank=True, null=True, editable=False)
    
    # Categorization.
    categories = models.ManyToManyField(Category, filter_interface=models.HORIZONTAL, blank=True)
    tag_list = models.CharField('Tags', maxlength=250, blank=True, null=True)
    
    objects = EntryManager()
    
    class Meta:
        get_latest_by = 'pub_date'
        ordering = ['-pub_date']
        unique_together = (('slug', 'pub_date'),)
        verbose_name_plural = 'Entries'
    
    class Admin:
        date_hierarchy = 'pub_date'
        fields = (
            ('Metadata', { 'fields':
                           ('title', 'slug', 'pub_date', 'author', 'status', 'enable_comments') }),
            ('Entry', { 'fields':
                        ('excerpt', 'body') }),
            ('Categorization', { 'fields':
                                 ('categories', 'tag_list') }),
            )
        list_display = ('title', 'pub_date', 'enable_comments')
        list_filter = ('status',)
        search_fields = ('excerpt', 'body', 'title')
    
    def save(self):
        # Run markup filter before save.
        if self.excerpt:
            self.excerpt_html = utils.apply_markup_filter(self.excerpt)
        self.body_html = utils.apply_markup_filter(self.body)
        super(Entry, self).save()
        
        # Update tags after saving, because we want to make sure
        # the Entry has an id for setting up relations.
        self.tags = self.tag_list
        
    def __str__(self):
        return self.title
    
    def get_absolute_url(self):
        return "/weblog/%s/%s/" % (self.pub_date.strftime("%Y/%b/%d").lower(), self.slug)
    
    def comments_open(self):
        """
        Used to determine whether an entry is old enough that new
        comments on it should await approval before becoming public.
        
        """
        return self.enable_comments and datetime.datetime.today() - datetime.timedelta(settings.COMMENTS_MODERATE_AFTER) <= self.pub_date
    
    def _get_tags(self):
        """
        Returns the set of Tag objects for this Entry.
        
        Access this via the ``tags`` property.
        
        """
        return Tag.objects.get_for_object(self)
    
    def _set_tags(self, tag_list):
        """
        Sets the Tag objects for this Entry.
        
        Access this via the ``tags`` property.
        
        """
        Tag.objects.update_tags(self, tag_list)
    
    tags = property(_get_tags, _set_tags)


class Link(models.Model):
    """
    A link posted to the weblog.
    
    Denormalized in the same fashion as the Entry model, in order to
    allow text-to-HTML conversion to be performed on the
    ``description`` field.
    
    """
    # Metadata.
    enable_comments = models.BooleanField(default=True)
    post_elsewhere = models.BooleanField('Post to del.icio.us',
                                         default=settings.DEFAULT_EXTERNAL_LINK_POST)
    posted_by = models.ForeignKey(User)
    pub_date = models.DateTimeField(default=datetime.datetime.today)
    title = models.CharField(maxlength=250)
    slug = models.SlugField(prepopulate_from=('title',))
    
    # The actual link bits.
    description = models.TextField()
    description_html = models.TextField(editable=False)
    via_name = models.CharField('Via', maxlength=250, blank=True, null=True,
                                help_text='The name of the person whose site you spotted the link on. Optional.')
    via_url = models.URLField('Via URL', verify_exists=False, blank=True, null=True,
                              help_text='The URL of the site where you spotted the link. Optional.')
    tag_list = models.CharField('Tags', maxlength=250, blank=True, null=True)
    url = models.URLField('URL', unique=True, verify_exists=False)
    
    class Meta:
        ordering = ['-pub_date']
        unique_together = (('slug', 'pub_date'),)
    
    class Admin:
        date_hierarchy = 'pub_date'
        fields = (
            ('Metadata', { 'fields':
                           ('title', 'slug', 'pub_date', 'posted_by', 'enable_comments', 'post_elsewhere') }),
            ('Link', { 'fields':
                      ('url', 'description', 'via_name', 'via_url', 'tag_list') }),
            )
        list_display = ('title', 'enable_comments')
        search_fields = ('title', 'description')
    
    def save(self):
        if not self.id and self.post_elsewhere:
            import pydelicious
            try:
                pydelicious.add(settings.DELICIOUS_USER, settings.DELICIOUS_PASSWORD, self.url, self.title, self.tag_list)
            except:
                pass # TODO: don't just silently quash a bad del.icio.us post
        self.description_html = utils.apply_markup_filter(self.description)
        super(Link, self).save()
        self.tags = self.tag_list
    
    def __str__(self):
        return self.title
    
    def get_absolute_url(self):
        return "/weblog/links/%s/%s/" % (self.pub_date.strftime("%Y/%b/%d").lower(), self.slug)
    
    def comments_open(self):
        """
        Used to determine whether an entry is old enough that new
        comments on it should await approval before becoming public.
        
        """
        return self.enable_comments and datetime.datetime.today() - datetime.timedelta(settings.COMMENTS_MODERATE_AFTER) <= self.pub_date
    
    def _get_tags(self):
        """
        Returns the set of Tag objects for this Link.
        
        Access this via the ``tags`` property.
        
        """
        return Tag.objects.get_for_object(self)
    
    def _set_tags(self, tag_list):
        """
        Sets the Tag objects for this Link.
        
        Access this via the ``tags`` property.
        
        """
        Tag.objects.update_tags(self, tag_list)
    
    tags = property(_get_tags, _set_tags)
