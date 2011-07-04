#########################################################################
#
# NotreDAM, Copyright (C) 2009, Sardegna Ricerche.
# Email: labcontdigit@sardegnaricerche.it
# Web: www.notre-dam.org
#
# This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#########################################################################

from django.shortcuts import render_to_response
from django.template import RequestContext, Context, loader
from django.template.loader import render_to_string
from django.http import HttpResponse, HttpResponseServerError
from django.utils import simplejson
from django.views.generic.simple import redirect_to
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.db import transaction
import shutil, os
from django.conf import settings

#from dam.scripts.models import Pipeline
from dam.mprocessor.models import Pipeline, Process
from dam.repository.models import Item, Component, Watermark, new_id, get_storage_file_name
from dam.core.dam_repository.models import Type
from dam.metadata.models import MetadataDescriptorGroup, MetadataDescriptor, MetadataValue, MetadataProperty
from dam.variants.models import Variant
from dam.treeview.models import Node
#from dam.batch_processor.models import MachineState, Machine, Action
from dam.workspace.models import DAMWorkspace as Workspace
from dam.core.dam_workspace.decorators import permission_required
from dam.upload.models import UploadURL
from dam.upload.uploadhandler import StorageHandler
from dam.eventmanager.models import EventRegistration
from dam.preferences.views import get_metadata_default_language
#from dam.mprocessor.models import MAction

from mediadart.storage import Storage

from dam.logger import logger
import mimetypes
import os.path, traceback
import time
import tempfile
from django.core.files.uploadedfile import TemporaryUploadedFile
from  django.core.files.uploadhandler import TemporaryFileUploadHandler

class NDUploadedFile(TemporaryUploadedFile):
    def __init__(self, dir, name, content_type, size, charset):
        self.file_name = os.path.join(dir, name)
        file = open(self.file_name, 'wb+')
        self.dir = dir        
        super(TemporaryUploadedFile, self).__init__(file, name, content_type, size, charset)
        
class NDTemporaryFileUploadHandler(TemporaryFileUploadHandler):
    def __init__(self, dir, *args, **kwargs):
        
        self.dir = dir            
        super(NDTemporaryFileUploadHandler, self).__init__(*args, **kwargs)
    
    def new_file(self, file_name, *args, **kwargs):
        super(TemporaryFileUploadHandler, self).new_file(file_name, *args, **kwargs)
        self.file = NDUploadedFile(self.dir, self.file_name, self.content_type, 0, self.charset)

def _get_upload_url(user, workspace, number):

    """
    FIX: API NEED FIXING!!
    """

    urls = []

    for i in xrange(int(number)):
        urls.append(UploadURL.objects.create(user=user, workspace=workspace).url)

    return urls

@login_required
def get_upload_url(request):
    """
    Generate unique upload urls (workaround for flash cookie bug)
    """
    n = request.POST['n']

    workspace = request.session.get('workspace')
    user = User.objects.get(pk = request.session['_auth_user_id'])

    urls = _get_upload_url(user, workspace, n)

    resp = simplejson.dumps({'urls': urls})
    return HttpResponse(resp)
    
def _get_uploaded_info(upload_file):
    if not isinstance(upload_file.name, unicode):
        file_name = unicode(upload_file.name, 'utf-8')
    else:
        file_name = upload_file.name

    type = guess_media_type(file_name)
    
    upload_file.rename()
    
    res_id = upload_file.get_res_id()

    return (file_name, type, res_id)


@transaction.commit_manually
def _save_uploaded_component(request, res_id, file_name, variant, item, user, workspace):
    """
    Create component for the given item and generate mediadart tasks. 
    Used only when user uploaded an item's variant
    """
    try:
        logger.debug('############### _save_uploaded_component: %s' % variant.pk)
        comp = item.create_variant(variant, workspace)
        
        if variant.auto_generated:
            comp.imported = True
    
        logger.debug('comp._id %s'%comp._id)
        logger.debug('res_id %s'%res_id)
        comp.file_name = file_name
        comp._id = res_id
        
        mime_type = mimetypes.guess_type(file_name)[0]
            
        ext = mime_type.split('/')[1]
        comp.format = ext
        comp.save()
        
        default_language = get_metadata_default_language(user)
        
        for key in request.POST.keys():
            if key.startswith('metadata'):
                value = request.POST[key]
                metadata_id = key.split('_')[-1]
                descriptor = MetadataDescriptor.objects.get(pk = metadata_id)
                MetadataValue.objects.save_descriptor_values(descriptor, item, value, workspace, 'original', default_language)
    
        metadataschema_mimetype = MetadataProperty.objects.get(namespace__prefix='dc',field_name='format')
        orig=MetadataValue.objects.create(schema=metadataschema_mimetype, content_object=comp,  value=mime_type)
        transaction.commit()
       
        generate_tasks(comp, workspace)
        
#-        if not variant.auto_generated:
#-            for ws in item.workspaces.all():
#-                EventRegistration.objects.notify('upload', workspace,  **{'items':[item]})
        
    except Exception, ex:
        print traceback.print_exc(ex)
        transaction.rollback()
        raise    
    transaction.commit()
    
    
def _save_uploaded_item(request, upload_file, user, workspace):

    variant = Variant.objects.get(name = 'original')

    file_name, type, res_id = _get_uploaded_info(upload_file)
    
    item_ctype = ContentType.objects.get_for_model(Item)

    
    media_type = Type.objects.get(name=type)
    
    item = Item.objects.create(owner = user, uploader = user,  type = media_type)
    item_id = item.pk
    item.add_to_uploaded_inbox(workspace)

    item.workspaces.add(workspace)

    _save_uploaded_component(request, res_id, file_name, variant, item, user, workspace)
#    EventRegistration.objects.notify('upload', workspace,  **{'items':[item]})
    
def _save_uploaded_variant(request, upload_file, user, workspace):
    variant_id = request.POST['rendition_id']
    item_id = request.POST['item_id']

    variant =  Variant.objects.get(pk = variant_id)
    logger.debug('***************************************VARIANT %s******************'%variant)
    item = Item.objects.get(pk = item_id)
    
    file_name, type, res_id = _get_uploaded_info(upload_file)
                
    _save_uploaded_component(request, res_id, file_name, variant, item, user, workspace)


        


def _create_item(user, workspace, res_id, media_type):
    item = Item.objects.create(owner = user, uploader = user,  _id = res_id, type=media_type)    
    item.add_to_uploaded_inbox(workspace)    
    item.workspaces.add(workspace)
    return item

#def _get_media_type(file_name):
#    type = guess_media_type(file_name)
#    media_type = Type.objects.get(name=type)
#    return media_type

def _create_variant(file_name, uri, media_type, item, workspace, variant):
    logger.debug('####### create_variant#########')
    logger.debug('file_name %s'%file_name)
    logger.debug('uri %s'%uri)
    
    comp = item.create_variant(variant, workspace, media_type)        
    if not item.type:
        item.type = media_type
    if variant.auto_generated:
        comp.imported = True
    
    comp.file_name = file_name
    comp.uri = uri        
    #mime_type = mimetypes.guess_type(file_name)[0]
    #ext = mime_type.split('/')[1]
    comp.format = media_type.ext   # bad bad
    comp.save()    
    return comp

def _get_filepath(file_name):
    fname, ext = os.path.splitext(file_name)
    res_id = new_id() + ext
    fpath = os.path.join(settings.MEDIADART_STORAGE, res_id)
    
    return fpath, res_id

def _run_pipelines(items, trigger, user, workspace, params = {}):
    """
       Parameters:
       <items> list of items on which to run the pipelines associated to trigger.
       <trigger> is a string, like 'upload'
       <user> and <workspace> are two objects as usual.

       Find all the pipelines associated to the trigger;
       Associates each item to all pipelines that accept that type of item;
       Launches all pipes;
       Returns the list of process_id launched;
    """
    assigned_items = set()
    ret = []
    for pipe in Pipeline.objects.filter(triggers__name=trigger, workspace = workspace):
        process = None
        for item in items:
            if pipe.is_compatible(item.type):
                if process is None:
                    process = Process.objects.create(pipeline=pipe, workspace=workspace, launched_by=user)
                
                process.add_params(item.pk, params)
                assigned_items.add(item)
                logger.debug('item %s added to %s' % (item.pk, pipe.name))
        if process:
            logger.debug( 'Launching process %s-%s' % (str(process.pk), pipe.name))
            ret.append(str(process.pk))
            process.run()

    unassigned = assigned_items.symmetric_difference(items)
    if unassigned:
        logger.debug("##### The following items have no compatible  action: " )
    return ret

def _create_items(filenames, variant_name, user, workspace, make_copy=True):
    """
       Parameters:
       <filenames> is a list of tuples (filename, original_filename, res_id).
       <variant_name> is the name of a registered variant
       <user> and <workspace> are two objects as usual.

       Creates a new item for each filename;
       Create a component with variant = variant_name;
       Returns the list of items created;
    """
    items = []
    logger.debug("filenames : %s" %filenames)
    for original_filename in filenames:        
        
        res_id = new_id()
        variant = Variant.objects.get(name = variant_name)
        media_type = Type.objects.get_or_create_by_filename(original_filename)        
        final_filename = get_storage_file_name(res_id, workspace.pk, variant.name, media_type.ext)
        final_path = os.path.join(settings.MEDIADART_STORAGE, final_filename)
        upload_filename = os.path.basename(original_filename)
        
        tmp = upload_filename.split('_')
        item = _create_item(user, workspace, res_id, media_type)
        if len(tmp) > 1:
            upload_filename = '_'.join(tmp[1:])
        _create_variant(upload_filename, final_filename, media_type, item, workspace, variant)
        if make_copy:
            shutil.copyfile(original_filename, final_path)
        else:
            shutil.move(original_filename, final_path)
        items.append(item)
    return items


def import_dir(dir_name, user, workspace, variant_name = 'original', trigger = 'upload', make_copy = False, recursive = True):
    logger.debug('########### INSIDE import_dir: %s' % dir_name)
    #files = [os.path.join(dir_name, x) for x in os.listdir(dir_name)]
    items = []
    for entry in os.walk(dir_name):
        root_dir, sub_dirs, files = entry
        files = [os.path.join(root_dir, x) for x in files]
        items.extend(_create_items(files, variant_name, user, workspace, make_copy))    
        if not recursive:
            break
        
    if trigger:
        ret = _run_pipelines(items, trigger,  user, workspace)
    logger.debug('items %s'%items)
    return items
    #logger.debug('Launched %s' % ' '.join(ret))

def _upload_item(file_name, file_raw,  variant, user, tmp_dir, workspace, session_finished = False):
    logger.debug('_upload_item %s in %s'%(file_name, tmp_dir))
    file_name = new_id() + '_' + file_name
    file = open(os.path.join(tmp_dir, file_name), 'wb+')
    file.write(file_raw)
    file.close() 
	
def _upload_resource_via_post(request, use_session = True):
    import tempfile
            
    tmp_dir = tempfile.mkdtemp()
    request.upload_handlers = [NDTemporaryFileUploadHandler(dir = tmp_dir)]
    logger.debug('request.FILES %s'%request.FILES)
    logger.debug('request.POST %s'%request.POST)
    
    if use_session:
        session = request.POST['session']
        real_tmp_dir = get_tmp_dir(session) #since i cant acces to request before settings upload handler
        os.rename(tmp_dir, real_tmp_dir)
        tmp_dir = real_tmp_dir
    return tmp_dir
    
def get_tmp_dir(session):
    return '/tmp/'+ session
        
@login_required
def upload_item(request):

    """
    Used for uploading a new item. Save the uploaded file using the custom handler dam.upload.uploadhandler.StorageHandler
    """
    from urllib import unquote
    
    def check_dir_session(session):
        tmp_dir = get_tmp_dir(session)
        if not os.path.exists(tmp_dir):
            os.mkdir(tmp_dir)  
        return tmp_dir
    
    try:
            
        workspace = request.session['workspace']
        
        user = request.user  
        
        if request.GET: #passing file in request.raw_post_data, other params in GET
            file_name = unquote(request.META['HTTP_X_FILE_NAME'])
            session = request.GET['session']
            check_dir_session(session)
            variant_name = request.GET['variant']
            variant = Variant.objects.get(name = variant_name)
            
            file_counter = int(request.GET['counter'])
            total = int(request.GET['total'])
            tmp_dir = check_dir_session(session)
            _upload_item(file_name, request.raw_post_data, variant, request.user, tmp_dir, workspace)
                    
            
            
        else: # files in request.FILES
            logger.debug('upload item via post multipart/form')                     
            _upload_resource_via_post(request)     
            
        resp = simplejson.dumps({'success': True})
        
    except Exception, ex:
        logger.exception(ex)
        raise ex        
    
    return HttpResponse(resp)

def _upload_resource_via_raw_post_data(request):
    import tempfile
    file_h, path  = tempfile.mkstemp()
    file_obj = open(path, 'wb')
    file_obj.write(request.raw_post_data)
    file_obj.close()
    logger.debug('file_obj.name %s'% file_obj.name)
    
    return file_obj
    

def _upload_variant(item, variant, workspace, user, file_name, file):
    """ 
    
    """   
    try:
        logger.debug('user %s'%user)
        #TODO: refresh url in gui, otherwise old variant will be shown
        if not isinstance(file_name, unicode):
            file_name = unicode(file_name, 'utf-8')
            
        ext = os.path.splitext(file_name)[1]
        res_id = item.ID
        
        final_file_name = get_storage_file_name(res_id, workspace.pk, variant.name, ext)    
        final_path = os.path.join(settings.MEDIADART_STORAGE, final_file_name)
        logger.debug('before move')
        
        if hasattr(file, 'file_name'):
            tmp_file_name = file.file_name
        else:
            tmp_file_name = file.name
            
        shutil.move(tmp_file_name , final_path)
        
        
        media_type = Type.objects.get_or_create_by_filename(file_name)
         
        _create_variant(file_name, final_file_name,media_type, item, workspace, variant)    
        uploaders = []
        if variant.name == 'original':
            triggers_name = 'upload'
            params = {}
        else:
            triggers_name = 'replace_rendition'
            params = {'*':{'source_variant_name': variant.name}}
        _run_pipelines([item], triggers_name, user, workspace, params)
    except Exception, ex:
        logger.exception(ex)
        raise ex
        
@login_required
def upload_variant(request):  
    logger.debug('UPLOAD VARIANT')
    workspace = request.session['workspace']
    user = request.user  
    if request.GET:    
        variant_name = request.GET['variant']
        logger.debug('--- variant_name %s'%variant_name)
        
        variant = Variant.objects.get(name = variant_name)
        item_id = request.GET.get('item')        
        
        
        file_name = request.META['HTTP_X_FILE_NAME']
        file = _upload_resource_via_raw_post_data(request)
        
    else:
        
        file = _upload_resource_via_post(request, False)
        logger.debug('file %s'%file)
        logger.debug('request.FILES %s'%request.FILES)
        file = request.FILES['files_to_upload']
        
        
        variant_name = request.POST['variant']
        logger.debug('variant_name %s'%variant_name)
        variant = Variant.objects.get(name = variant_name)
        
        item_id = request.POST.get('item')
        file_name = file.name
    
    item = Item.objects.get(pk = item_id)
       
    logger.debug('before _upload_variant, file_name %s'%file_name)
    _upload_variant(item, variant, workspace, request.user, file_name, file)
    

    
    resp = simplejson.dumps({'success': True})        
   
    return HttpResponse(resp)

def upload_watermark(request):
    """
    Used for uploading/replacing the watermark for the given workspace. Save the uploaded file using the custom handler dam.upload.uploadhandler.StorageHandler
    """
       
    request.upload_handlers = [StorageHandler()]
    url = request.POST['unique_url']    
    upload_file = request.FILES['Filedata']

    user, workspace = UploadURL.objects.get_user_ws_from_url(url)

    file_name, type, res_id = _get_uploaded_info(upload_file)

    logger.debug('file_name %s' % file_name)
    logger.debug('res_id %s' % res_id)

    comp= Watermark.objects.create(type = Type.objects.get(name=type), workspace=workspace)

    comp.file_name = file_name
    comp._id = res_id
    comp.save()
    
    resp = simplejson.dumps({'res_id': res_id})
    return HttpResponse(resp)

@login_required
def get_metadata_upload(request):
    """
    Get metadata schemas for upload 
    """
    user = request.session['_auth_user_id']
    workspace = request.session['workspace']
    metadata = {'schemas': []}
    if workspace.get_permissions(user).filter(Q(codename = 'edit_metadata') | Q(codename = 'admin')).count() > 0:
        group = MetadataDescriptorGroup.objects.get(upload=True)
        
        for ms in group.descriptors.all():
            metadata['schemas'].append({'pk':ms.pk,  'name':ms.name.capitalize()})
    
    resp = simplejson.dumps(metadata)
    return HttpResponse(resp)

def guess_media_type (file):

    """
    It tries to guess the media type of the uploaded file (image, video, audio or doc)
    """
    
#    mimetypes.init()
    mimetypes.add_type('video/flv','.flv')
    mimetypes.add_type('video/ts','.ts')
    mimetypes.add_type('video/mpeg4','.m4v')
    mimetypes.add_type('doc/pdf','.pdf')
    mimetypes.add_type('image/nikon', '.nef')
    mimetypes.add_type('image/canon', '.cr2')
    mimetypes.add_type('image/digitalnegative', '.dng')
    
    media_type = mimetypes.guess_type(file)
    
    try:
        media_type = media_type[0][:media_type[0].find("/")]
        
        if media_type not in [type.name for type in Type.objects.all()]:
            raise Exception
            
    except Exception, ex:
        media_type = 'media_type sconociuto'
        logger.debug('raise ex')
        raise Exception('unsupported media type')

    return media_type

def upload_session_finished(request):
    session = request.POST['session']
    workspace = request.session.get('workspace')
    user = User.objects.get(pk = request.session['_auth_user_id'])
    
    tmp_dir = '/tmp/'+ session
    logger.debug('tmp_dir %s'%tmp_dir)
    
    if os.path.exists(tmp_dir):
        uploads_success = import_dir(tmp_dir, user, workspace)
        os.rmdir(tmp_dir)
        return HttpResponse(simplejson.dumps({'success': True, 'uploads_success': len(uploads_success)}))
    else:
        return HttpResponse(simplejson.dumps({'success': False}))
    
        
