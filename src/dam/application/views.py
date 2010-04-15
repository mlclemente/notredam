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
from django.template import RequestContext, Context, loader,  Template
from django.views.generic.simple import redirect_to
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.contrib.contenttypes.models import ContentType

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db.models import Q
from django.core.paginator import Paginator, InvalidPage
from django.core.mail import send_mail
from django.utils import simplejson
from django.contrib.admin.views.decorators import staff_member_required

from dam.variants.models import Variant,  VariantAssociation
from dam.repository.models import Item, Component
from dam.workspace.models import Workspace
from dam.workspace.decorators import permission_required
from dam.settings import EMAIL_SENDER, SERVER_PUBLIC_ADDRESS,  MEDIADART_CONF
from dam.application.forms import Registration
from dam.application.models import VerificationUrl

from mediadart import toolkit

import os
import cPickle as pickle
import logger

#NOTAVAILABLE = '/files/images/notavailable.jpg'
NOTAVAILABLE = None
 
def home(request, msg=None):
    """
    Always redirect user from http://host/ to http://host/workspace
    """
    return redirect_to(request, '/workspace/')

@staff_member_required
def dam_admin(request):
    """
    TODO: Dam Administration Interface is under development
    """
    return render_to_response('dam_administration.html', RequestContext(request,{}))

def do_login(request):
    """
    Manage user login
    """
    username = request.POST['username']
    password = request.POST['password']
    user = authenticate(username=username, password=password)
    if user is not None:
        if user.is_active:
            login(request, user)
            return home(request)
        else:
            return home(request, "User disabled.")
    else:
        return HttpResponseForbidden("User or/and password wrong. Please check and try again.")

def do_logout(request):
    """
    User logout
    """
    logout(request)
    if request.session.__contains__('workspace'):
        request.session.__delitem__('workspace')
    return home(request)

def get_component_url(workspace, item_id, variant_name,  public_only=False,  thumb = False,  redirect_if_not_available = True):
    """
    Looks for the component named variant_name of the item item_id in the given workspace and returns the component url 
    """
    logger.debug('variant_name %s'%variant_name)    
    
    item = Item.objects.get(pk = item_id)
#    logger.debug('workspace.get_variants() %s'%workspace.get_variants())
    variant = workspace.get_variants().distinct().get(media_type__name =  item.type,  name = variant_name)
#    logger.debug('variant.default_url %s'%variant.default_url)
    
    if thumb and variant.default_url:
        return variant.default_url
    
    url = NOTAVAILABLE    
   
    if public_only:
#        component = Component.objects.get(item__pk = item_id,  item__is_public = True, variant__name = component_name)
        pass
    else:
        try:
            component = variant.get_component(workspace,  item)
        
#            logger.debug('component.uri %s'%component.uri)
#            logger.debug('component.ID %s'%component.ID)
            if component.uri:
                return component.uri

            t = toolkit.Toolkit(MEDIADART_CONF)
            storage = t.get_storage()
            
            url = storage.get_resource_url(component.ID)[0]
        except Exception,ex:
            logger.exception( 'ex in get_component_url %s' %  ex )
            url = NOTAVAILABLE

        #        logger.debug( "Url da storage for component %s of item %s: %s" % (component.pk, item_id, url) )
    if url == NOTAVAILABLE and not redirect_if_not_available:
        return None
        
    return url


def _get_resource_url(id):
    t = toolkit.Toolkit(MEDIADART_CONF)
    storage = t.get_storage()

    try:
        url = storage.get_resource_url(id)[0]
        logger.debug('URL %s' %url)
    except:
        url = None
    return url

    

@login_required
def redirect_to_resource(request, id):
    """
    Redirects to the resource url given by MediaDART
    """
    t = toolkit.Toolkit(MEDIADART_CONF)
    storage = t.get_storage()

    try:
        url = storage.get_resource_url(id)[0]
        logger.debug('URL %s' %url)
    except:
        url = NOTAVAILABLE    
    return redirect_to(request,  url)

@login_required
def get_component(request, item_id, variant_name ,  redirect=False):
    """
    Looks for the component named variant_name of the item item_id in the given workspace
    """
    workspace = request.session.get('workspace', None) 
    if workspace is None:
        public_only = True
    else:
        public_only = False
    
    if request.GET.get('thumb'):
        thumb = True
    else:
        thumb = False
    
    try:
        url = get_component_url(workspace,  item_id, variant_name, public_only = public_only,  thumb = thumb)
    except Exception,  ex:
        logger.exception(ex)
        return HttpResponse(simplejson.dumps({'failure': True}))
    if redirect:
        if request.GET.get('download',  0):
            url = os.path.join(url, 'download/')
        return redirect_to(request, url)
    else:
        return HttpResponse(url)

def redirect_to_component(request, item_id, variant_name,  ws_id = None):
    """
    Redirects to the component url
    """
    return get_component(request, item_id, variant_name, True)

def registration(request):
    """
    Creates registration form or saves registration info
    """
    from dam.workspace.views import _create_workspace

    def save_user_and_login(form):
        form.save(commit = True)
        user = authenticate(username = form.cleaned_data['username'], password =  form.cleaned_data['password1'])
        login(request,  user)
        ws = Workspace(name=user.username)
        _create_workspace(ws,  user)
        return user

    if request.method == 'POST': 
        logger.debug('POST %s'%request.POST)
        form = Registration(request.POST) 

        if form.is_valid():
            user = save_user_and_login(form)
#            user = form.save()
#            user.is_active = True
#            user.save()
#            url = VerificationUrl.objects.create(user = user).url
#            final_url = 'http://%s/confirm_user/%s/'%(SERVER_PUBLIC_ADDRESS , url)

#            send_mail('Registration confirmation', 'Hi %s,\nclick at this url %s \n to confirm your registration at DAM.'%(user.username, final_url), EMAIL_SENDER, [user.email], fail_silently=False)

            resp = simplejson.dumps({'success': True, 'errors': []})
            return HttpResponse(resp)

        else:
            from django.template.defaultfilters import striptags
            errors = []
            for field in form:
                if field.errors:
                    errors.append(striptags(field.errors))

            resp = simplejson.dumps({'success': False, 'errors': errors})
            return HttpResponse(resp)
    else:

        form = Registration()

    return render_to_response('registration.html', RequestContext(request,{'form': form }))

def confirm_user(request, url):
    """
    Confirms user registration and creates a new workspace for the new user
    """
    from dam.workspace.views import _create_workspace
    user = VerificationUrl.objects.get(url= url).user
    user.is_active = True
    user.save()
    logout(request)
    logger.debug('user %s'%user)
    logger.debug('user.username %s'%user.username)
    logger.debug('user.password %s'%user.password)


    ws = Workspace(name=user.username)
    _create_workspace(ws,  user)

    return render_to_response('registration_completed.html', RequestContext(request,{'usr':user}))
