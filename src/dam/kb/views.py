#########################################################################
#
# NotreDAM, Copyright (C) 2011, Sardegna Ricerche.
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
#
# Django views for interacting with the NotreDAM knowledge base
#
# Author: Alceste Scalas <alceste@crs4.it>
#
#########################################################################

import datetime

from django.contrib.auth.decorators import login_required
from django.http import (HttpRequest, HttpResponse, HttpResponseNotFound,
                         HttpResponseNotAllowed, HttpResponseBadRequest,
                         HttpResponseForbidden)
from django.utils import simplejson

from dam.core.dam_workspace.decorators import permission_required

import tinykb.access as kb_access
import tinykb.session as kb_ses
import tinykb.classes as kb_cls
import tinykb.errors as kb_exc
import tinykb.attributes as kb_attrs
import util

# FIXME: use the standard ModResource-based dispatch system here

@login_required
@permission_required('admin', False)
def class_index(request, ws_id):
    '''
    GET: return the list of classes defined in the knowledge base.
    PUT: insert a new class in the knowledge base.
    '''
    return _dispatch(request, {'GET' : class_index_get,
                               'PUT' : class_index_put},
                     {'ws_id' : int(ws_id)})


def class_index_get(request, ws_id):
    ses = _kb_session()

    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    cls_dicts = [_kbclass_to_dict(c) for c in ses.classes(ws=ws)]

    return HttpResponse(simplejson.dumps(cls_dicts))


def class_index_put(request, ws_id):
    try:
        cls_dict = _assert_return_json_data(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    ses = _kb_session()

    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    class_name = cls_dict.get('name', None)
    if class_name is None:
        return HttpResponseBadRequest('Class representation lacks a '
                                      +'"name" field')

    superclass = None
    superclass_id = cls_dict.get('superclass', None)
    if superclass_id is not None:
        try:
            superclass = ses.class_(superclass_id)
        except kb_exc.NotFound:
            return HttpResponseBadRequest('Unknown superclass id: "%s"'
                                          % (superclass_id, ))

    explicit_id = cls_dict.get('id', None)
    if explicit_id is not None:
        # Check for uniqueness
        try:
            ses.class_(explicit_id)
            return HttpResponseBadRequest('Class id "%s" already in use'
                                          % (explicit_id, ))
        except kb_exc.NotFound:
            pass

    # Build the list of attributes of the class
    json_attrs = cls_dict.get('attributes', [])
    attrs = []
    for attr_id in json_attrs:
        a = json_attrs[attr_id]
        try:
            attr_type = a['type']
        except KeyError:
            return HttpResponseBadRequest('Attribute "%s" lacks a "type" field'
                                          % (attr_id, ))

        try:
            attr_fn = _kb_dict_attrs_map[attr_type]
        except KeyError:
            return HttpResponseBadRequest(('Attribute "%s" has an invalid '
                                           + 'type: "%s"')
                                          % (attr_id, attr_type))

        try:
            attr_obj = attr_fn(a)
        except KeyError as e:
            return HttpResponseBadRequest(('Attribute "%s" (type %s) lacks '
                                           + 'a required field: "%s"')
                                          % (attr_id, attr_type, str(e)))

        attrs.append(attr_obj)

    if superclass is None:
        cls = kb_cls.KBRootClass(class_name, explicit_id=explicit_id,
                                 attributes=attrs)
        # We also need to configure the visibility of the root class
        # on DAM workspaces
        resp = _setup_kb_root_class_visibility(request, ses, cls, cls_dict, ws)

        if resp is not None:
            # An error has occurred
            return resp
    else:
        cls = kb_cls.KBClass(class_name, superclass=superclass,
                             explicit_id=explicit_id, attributes=attrs)

    # FIXME: right now, we only support updating a few fields
    updatable_fields = {'name'        : set([unicode, str]),
                        'notes'       : set([unicode, str])}
    try:
        _assert_update_object_fields(cls, cls_dict, updatable_fields)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    ses.add(cls)
    ses.commit()

    return HttpResponse(cls.id)


@login_required
@permission_required('admin', False)
def class_(request, ws_id, class_id):
    '''
    GET: return a specific class from the knowledge base.
    POST: update an existing class in the knowledge base.
    DELETE: delete and existing class from the knowledge base.
    '''
    return _dispatch(request, {'GET'  : class_get,
                               'POST' : class_post},
                     {'ws_id' : int(ws_id),
                      'class_id' : class_id})


def class_get(request, ws_id, class_id):
    ses = _kb_session()
    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    try:
        cls = ses.class_(class_id, ws=ws)
    except kb_exc.NotFound:
        return HttpResponseNotFound()

    return HttpResponse(simplejson.dumps(_kbclass_to_dict(cls)))


def class_post(request, ws_id, class_id):
    try:
        cls_dict = _assert_return_json_data(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    ses = _kb_session()
    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    try:
        cls = ses.class_(class_id, ws=ws)
    except kb_exc.NotFound:
        return HttpResponseNotFound()

    perm = cls.workspace_permission(ws)
    if perm not in (kb_access.OWNER, kb_access.READ_WRITE):
        return HttpResponseForbidden()

    # If the current workspace doesn't own the class root, then ignore
    # the new workspaces access configuration (if any)
    # FIXME: maybe check whether access rules were changed, and raise an err?
    if perm != kb_access.OWNER:
        cls_dict['workspaces'] = _kb_class_visibility_to_dict(cls)

    # In case we're dealing with a root class, also consider its
    # visibility
    if isinstance(cls, kb_cls.KBRootClass):
        resp = _setup_kb_root_class_visibility(request, ses, cls, cls_dict, ws)
        if resp is not None:
            # An error has occurred
            return resp

    # FIXME: right now, we only support updating a few fields
    updatable_fields = {'name'        : set([unicode, str]),
                        'notes'       : set([unicode, str])}
    try:
        _assert_update_object_fields(cls, cls_dict, updatable_fields)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    ses.add(cls)
    ses.commit()

    return HttpResponse('ok')


@login_required
@permission_required('admin', False)
def object_index(request, ws_id):
    '''
    GET: return the list of objects defined in the knowledge base.
    PUT: insert a new object in the knowledge base.
    '''
    return _dispatch(request, {'GET' : object_index_get,
                               'PUT' : object_index_put},
                     {'ws_id' : int(ws_id)})


def object_index_get(request, ws_id):
    ses = _kb_session()

    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    obj_dicts = [_kbobject_to_dict(o) for o in ses.objects(ws=ws)]

    return HttpResponse(simplejson.dumps(obj_dicts))


def object_index_put(request, ws_id):
    try:
        obj_dict = _assert_return_json_data(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    ses = _kb_session()

    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    object_class_id = obj_dict.get('class', None)
    if object_class_id is None:
        return HttpResponseBadRequest('Object representation lacks a '
                                      +'"class" field')

    object_name = obj_dict.get('name', None)
    if object_name is None:
        return HttpResponseBadRequest('Object representation lacks a '
                                      +'"name" field')
    
    explicit_id = obj_dict.get('id', None)
    
    if explicit_id is not None:
        # Check for uniqueness
        try:
            ses.object(explicit_id)
            return HttpResponseBadRequest('Object id "%s" already in use'
                                          % (explicit_id, ))
        except kb_exc.NotFound:
            pass

    try:
        ObjectClass = ses.python_class(object_class_id, ws=ws)
    except kb_exc.NotFound:
        return HttpResponseBadRequest('Invalid object class: %s'
                                      % (object_class_id, ))

    obj = ObjectClass(object_name, explicit_id=explicit_id)

    # FIXME: right now, we only support updating a few fields
    updatable_fields = {'name'        : set([unicode, str]),
                        'notes'       : set([unicode, str])}

    try:
        _assert_update_object_fields(obj, obj_dict, updatable_fields)
        _assert_update_object_attrs(obj, obj_dict.get('attributes', {}), ses)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    ses.add(obj)
    ses.commit()

    return HttpResponse(obj.id)


@login_required
@permission_required('admin', False)
def object_(request, ws_id, object_id):
    '''
    GET: return a specific object from the knowledge base.
    POST: update an existing object in the knowledge base.
    DELETE: delete and existing object from the knowledge base.
    '''
    return _dispatch(request, {'GET' :  object_get,
                               'POST' : object_post},
                     {'ws_id' : int(ws_id),
                      'object_id' : object_id})


def object_get(request, ws_id, object_id):
    ses = _kb_session()
    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    try:
        cls = ses.object(object_id, ws=ws)
    except kb_exc.NotFound:
        return HttpResponseNotFound()

    return HttpResponse(simplejson.dumps(_kbobject_to_dict(cls)))


def object_post(request, ws_id, object_id):
    try:
        obj_dict = _assert_return_json_data(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    ses = _kb_session()
    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    try:
        obj = ses.object(object_id, ws=ws)
    except kb_exc.NotFound:
        return HttpResponseNotFound()

    # FIXME: right now, we only support updating a few fields
    updatable_fields = {'name'        : set([unicode, str]),
                        'notes'       : set([unicode, str])}
    try:
        _assert_update_object_fields(obj, obj_dict, updatable_fields)
        _assert_update_object_attrs(obj, obj_dict.get('attributes', {}), ses)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    ses.add(obj)
    ses.commit()

    return HttpResponse('ok')


@login_required
@permission_required('admin', False)
def class_objects(request, ws_id, class_id):
    '''
    GET: return the list of objects belonging to a given KB class.
    '''
    return _dispatch(request, {'GET' : class_objects_get},
                     {'ws_id' : int(ws_id),
                      'class_id' : class_id})


def class_objects_get(request, ws_id, class_id):
    ses = _kb_session()
    try:
        ws = ses.workspace(ws_id)
    except kb_exc.NotFound:
        return HttpResponseNotFound('Unknown workspace id: %s' % (ws_id, ))

    try:
        cls = ses.class_(class_id, ws=ws)
    except kb_exc.NotFound:
        return HttpResponseNotFound()

    objs = ses.objects(class_=cls.make_python_class())
    obj_dicts = [_kbobject_to_dict(o) for o in objs]

    return HttpResponse(simplejson.dumps(obj_dicts))


###############################################################################
# Internal helper functions
###############################################################################

def _dispatch(request, method_fun_dict, kwargs=None):
    '''
    Given a Django HttpRequest, choose the function associated to its
    HTTP method from method_fun_dict, and execute it.  If the third
    argument (kwargs) is not None, it is assumed to be a dictionary
    which will be expanded into additional keyword arguments for the
    function being called.

    The Django request passed to the function being invoked will also
    be enriched with an additional _vars attribute, containing the
    method-related variables (GET, POST, etc.)
    '''
    (method, mvars) = _infer_method(request)
    if (method not in method_fun_dict):
        return HttpResponseNotAllowed(method_fun_dict.keys())

    # Enrich request object with a new '._vars' attribute, containing
    # GET or POST variables
    request._vars = mvars

    if kwargs is None:
        return method_fun_dict[method](request)
    else:
        return method_fun_dict[method](request, **kwargs)


def _infer_method(req):
    '''
    Return a 2-tuple containing the "real" HTTP method of the given
    request (i.e. also considering whether the method encoding variable
    is set), and the method-related variables
    '''
    assert(isinstance(req, HttpRequest))

    encodable_methods = ('PUT', 'DELETE')
    encoding_get_var = '__REAL_HTTP_METHOD__'

    if (('POST' == req.method) and
        (encoding_get_var in req.GET)):
        enc_method = req.GET[encoding_get_var]
        assert(enc_method in encodable_methods)
        return (enc_method, req.POST)
    elif ('POST' == req.method):
        return ('POST', req.POST)
    elif ('GET' == req.method):
        return ('GET', req.GET)
    else:
        # Simply return the current method, with GET variables
        return (req.method, req.GET)


def _kb_session():
    '''
    Create a knowledge base session, using the NotreDAM connection parameters
    '''
    connstr = util.notredam_connstring()
    return kb_ses.Session(connstr)
    

def _kbclass_to_dict(cls):
    '''
    Create a JSON'able dictionary representation of the given KB class
    '''
    clsattrs = {}
    for a in cls.all_attributes():
        clsattrs[a.id] = _kbattr_to_dict(a)

    if (cls.superclass.id == cls.id):
        superclass = None
    else:
        superclass = cls.superclass.id

    clsdict = {'id'          : cls.id,
               'name'        : cls.name,
               'superclass'  : superclass,
               'notes'       : cls.notes,
               'attributes'  : clsattrs,
               'workspaces'  : _kb_class_visibility_to_dict(cls)}

    return clsdict


# Return a dictionary describing the access rules of a workspace to
# the given KB class
def _kb_class_visibility_to_dict(cls):
    # Internal mapping from access type to string
    access_str_map = {kb_access.OWNER      : 'owner',
                      kb_access.READ_ONLY  : 'read-only',
                      kb_access.READ_WRITE : 'read-write'}
    # Retrieve the root class
    while cls.superclass is not cls:
        cls = cls.superclass
    assert(isinstance(cls, kb_cls.KBRootClass)) # Just in case...

    vis_dict = {}
    for v in cls.visibility:
        vis_dict[v.workspace.id] = access_str_map[v.access]

    return vis_dict


# Mapping between attribute type and functions returning a JSON'able
# dictionary representation of the attribute itself
_kb_attrs_dict_map = {kb_attrs.Boolean : lambda a:
                          dict([['type',   'bool'],
                                ['default', a.default]]
                               + _std_attr_fields(a)),
                      kb_attrs.Integer : lambda a:
                          dict([['type',    'int'],
                                ['min',     a.min],
                                ['max',     a.max],
                                ['default', a.default]]
                               + _std_attr_fields(a)),
                      kb_attrs.Real    : lambda a:
                          dict([['type',    'real'],
                                ['min',     a.min],
                                ['max',     a.max],
                                ['default', a.default]]
                               + _std_attr_fields(a)),
                      kb_attrs.String  : lambda a:
                          dict([['type',    'string'],
                                ['length',  a.length],
                                ['default', a.default]]
                               + _std_attr_fields(a)),
                      kb_attrs.Date    : lambda a:
                          dict([['type',    'date'],
                                ['min',     a.min],
                                ['max',     a.max],
                                ['default', a.default]]
                               + _std_attr_fields(a)),
                      kb_attrs.Uri  : lambda a:
                          dict([['type',    'uri'],
                                ['length',  a.length],
                                ['default', a.default]]
                               + _std_attr_fields(a)),
                      kb_attrs.Choice  : lambda a:
                          dict([['type',    'choice'],
                                ['choices', simplejson.loads(a.choices)],
                                ['default', a.default]]
                               + _std_attr_fields(a)),
                      kb_attrs.ObjectReference : lambda a:
                          dict([['type',         'objref'],
                                ['target_class', a.target.id]]
                               + _std_attr_fields(a)),
                      kb_attrs.ObjectReferencesList : lambda a:
                          dict([['type',         'objref-list'],
                                ['target_class', a.target.id]]
                               + _std_attr_fields(a))                         
                      }


# This is the "inverse" of the mapping above: it associates a
# JSON'able dictionary representation of a class attribute with a
# function returning the actual Attribute object.  The function should
# raise a KeyError when a required dictionary field is missing
_kb_dict_attrs_map = {'bool' : lambda d:
                          kb_attrs.Boolean(default=d.get('default'),
                                           **(_std_attr_dict_fields(d))),
                      'int' : lambda d:
                          kb_attrs.Integer(min_=d.get('min'),
                                           max_=d.get('max'),
                                           default=d.get('default'),
                                           **(_std_attr_dict_fields(d))),
                      'real' : lambda d:
                          kb_attrs.Real(min_=d.get('min'),
                                        max_=d.get('max'),
                                        default=d.get('default'),
                                        **(_std_attr_dict_fields(d))),
                      'string' : lambda d:
                          kb_attrs.String(length=d['length'],
                                          default=d.get('default'),
                                          **(_std_attr_dict_fields(d))),
                      'date' : lambda d:
                          kb_attrs.Date(min_=d.get('min'),
                                        max_=d.get('max'),
                                        default=d.get('default'),
                                        **(_std_attr_dict_fields(d))),
                      'uri' : lambda d:
                          kb_attrs.Uri(length=d['length'],
                                       default=d.get('default'),
                                       **(_std_attr_dict_fields(d))),
                      'choice' : lambda d:
                          kb_attrs.Choice(list_of_choices=d['choices'],
                                          default=d.get('default'),
                                          **(_std_attr_dict_fields(d))),
                      'objref' : lambda d:
                          kb_attrs.ObjectReference(target_class=d['target_class'],
                                                 **(_std_attr_dict_fields(d))),
                      'objref-list' : lambda d:
                          kb_attrs.ObjectReference(target_class=d['target_class'],
                                                  **(_std_attr_dict_fields(d)))
                      }


def _kbattr_to_dict(attr):
    '''
    Create a string representation of a KB class attribute descriptor
    '''
    return _kb_attrs_dict_map[type(attr)](attr)


def _std_attr_fields(a):
    '''
    Return the standard fields present in each KB attribute object
    (non-null, notes, etc.)
    '''
    return [['name',        a.name],
            ['maybe_empty', a.maybe_empty],
            ['order',       a.order],
            ['notes',       a.notes]] 


def _std_attr_dict_fields(d):
    '''
    This is the "inverse" of _std_attr_fields(): return a dictionary
    that, when used as **kwargs, will give a value to the keyword
    arguments common to each Attribute sub-class constructor
    '''
    return {'name' : d['name'],
            'maybe_empty' : d.get('maybe_empty', True),
            'order' : d.get('order', 0),
            'notes' : d.get('notes')}


def _kbobject_to_dict(obj):
    '''
    Create a JSON'able dictionary representation of the given KB object
    '''
    objattrs = {}
    for a in getattr(obj, 'class').all_attributes():
        objattrs[a.id] = _kbobjattr_to_dict(a, getattr(obj, a.id))

    objdict = {'id'          : obj.id,
               'name'        : obj.name,
               'class'       : getattr(obj, 'class').id,
               'notes'       : obj.notes,
               'attributes'  : objattrs}

    return objdict


# Mapping between object attribute type and functions returning a
# JSON'able dictionary representation of the attribute value
_kb_objattrs_dict_map = {kb_attrs.Boolean : lambda a, v: v,
                         kb_attrs.Integer : lambda a, v: v,
                         kb_attrs.Real    : lambda a, v: v,
                         kb_attrs.String  : lambda a, v: v,
                         kb_attrs.Date    : lambda a, v: ((v is not None
                                                           and v.isoformat())
                                                          or None),
                         kb_attrs.String  : lambda a, v: v,
                         kb_attrs.Uri     : lambda a, v: v,
                         kb_attrs.Choice  : lambda a, v: v,
                         kb_attrs.ObjectReference: lambda a,v: ((v is not None
                                                                 and v.id)
                                                                or None),
                         kb_attrs.ObjectReferencesList : lambda a, v:
                             [o.id for o in v]
                         }

def _kbobjattr_to_dict(attr, val):
    '''
    Create a string representation of a KB class attribute descriptor
    '''
    return _kb_objattrs_dict_map[type(attr)](attr, val)


def _assert_return_json_data(request):
    '''
    Ensure that a Django request contains JSON data, and return it
    (taking care of its encoding).  Raise a ValueError if the content
    type is not supported.
    '''
    if ('application/json; charset=UTF-8' == request.META['CONTENT_TYPE']):
        return simplejson.loads(request.raw_post_data)
    else:
        # FIXME: we should support other charset encodings here
        raise ValueError('Unsupported content type: '
                         + request.META['CONTENT_TYPE'])


def _assert_update_object_fields(obj, obj_dict, updatable_fields):
    '''
    Update the fields (Python attributes) of an object, using the
    given dictionaries: one containing the new field/values, and
    another which associates each field name with the set of their
    expected type.  In case of error, a ValueError is raised (with an
    informative message).
    '''
    for f in updatable_fields:
        val = obj_dict.get(f, getattr(obj, f))
        expected_types = updatable_fields[f]
        if not type(val) in expected_types:
            raise ValueError(('Invalid type for attribute %s: '
                              + 'expected one of %s, got %s')
                             % (f, str(expected_types),
                                str(type(val))))
        setattr(obj, f, val)


def _assert_update_object_attrs(obj, obj_dict, sa_session):
    '''
    Almost like _assert_update_object_fields(), but applied to KB
    object attribute (i.e. with special care for object references and
    other "complex" types).
    '''
    obj_class_attrs = getattr(obj, 'class').all_attributes()
    for a in obj_class_attrs:
        val = obj_dict.get(a.id, getattr(obj, a.id))
        try:
            if (type(a) == kb_attrs.ObjectReference):
                # We can't simply update the attribute: we need to
                # retrieve the referred object by its ID
                curr_obj = getattr(obj, a.id)
                if (val == curr_obj.id):
                    # Nothing to be done here
                    break
                else:
                    try:
                        new_obj = sa_session.object(val)
                    except kb_exc.NotFound:
                        raise ValueError('Unknown object id reference: %s'
                                         % val)
                    # Actually perform the assignment
                    setattr(obj, a.id, new_obj)
            elif (type(a) == kb_attrs.ObjectReferencesList):
                # We can't simply update the attribute: we need to
                # retrieve the referred objects by their ID, and
                # add/remove them from the list
                obj_lst = getattr(obj, a.id)
                # Objects to be removed (i.e. whose ID is not present in
                # the list provided by the client)
                rm_objects = [o for o in obj_lst if o.id not in val]
                # Objects to be added
                curr_oids = [o.id for o in obj_lst]
                add_oids = [x for x in val if x not in curr_oids]
                add_objects = []
                for x in add_oids:
                    try:
                        add_objects.append(sa_session.object(x))
                    except kb_exc.NotFound:
                        raise ValueError('Unknown object id reference: %s'
                                         % x)
                # Actually perform removals/additions
                for o in rm_objects:
                    obj_lst.remove(o)
                for o in add_objects:
                    obj_lst.append(o)
            else:
                # Simple case: just update the attribute
                setattr(obj, a.id, val)
        except kb_exc.ValidationError, e:
            # One of the setattr() calls failed: re-raise the
            # exception with the proper error message
            raise ValueError(u'Error updating attribute "%s": %s'
                             % (a.id, unicode(e.parameter)))


# Configure the workspace visibility of the given KBRootClass (cls,
# with JSON'able representation cls_dict), using the given request and
# KB session.  'curr_ws' is the current workspace (which should appear
# among the class owners).  Return a HttpResponse object on failure,
# or None in case of success
def _setup_kb_root_class_visibility(request, ses, cls, cls_dict, curr_ws):
    cls_workspaces_dict = cls_dict.get('workspaces', None)
    if cls_workspaces_dict is None:
        return HttpResponseBadRequest('Root class representation lacks a '
                                      +'"workspaces" field')

    # Retrieve all the workspace IDs the current user has access to
    user_ws_ids = [w.id for w in request.user.workspaces.all()]
    
    owner_ws_list = []  # List of owner workspaces
    ws_list = []
    for (ws_id, access_str) in cls_workspaces_dict.items():
        try:
            ws_id = int(ws_id)
        except ValueError:
            return HttpResponseBadRequest(('Workspace id "%s" does not appear '
                                           + 'to be an integer') % (ws_id, ))

        try:
            ws = ses.workspace(ws_id)
        except kb_exc.NotFound:
            return HttpResponseBadRequest('Unknown workspace id: %s'
                                          % (ws_id, ))
        
        if ws_id not in user_ws_ids:
            return HttpResponseBadRequest(('Current user "%s" cannot share '
                                           + 'classes on workspace %s')
                                          % (request.user.username, ws_id ))
        
        ws_list.append(ws)
        
        # Translate the user-provided access string into a workspace
        # permission.  NOTE: this mapping MUST respect the one used in
        # _kb_class_visibility_to_dict()
        if (access_str == 'owner'):
            owner_ws_list.append(ws)
            access = kb_access.OWNER
        elif (access_str == 'read-only'):
            access = kb_access.READ_ONLY
        elif (access_str == 'read-write'):
            access = kb_access.READ_WRITE
        else:
            return HttpResponseBadRequest('Invalid permission for '
                                          'workspace %s: "%s"'
                                          % (ws_id, access_str))
        
        # Everything seems to be fine, let's share the class on
        # the given workspace(s)
        cls.setup_workspace(ws, access=access)
        
    # Also ensure that the current workspace is among the owners
    if curr_ws not in owner_ws_list:
        return HttpResponseBadRequest(('Root class representation does not'
                                       + ' report the current workspace (%d)'
                                       + ' as owner') % (ws_id, ))
        
    # Finally, remove the workspace visibilities which were not
    # mentioned in the class dictionary
    cls.restrict_to_workspaces(ws_list)

    # Everything is fine
    return None

