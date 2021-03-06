# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 OpenStack Foundation
# Copyright 2012 Justin Santa Barbara
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import datetime
from lxml import etree
import mox
from oslo.config import cfg
import uuid
import webob

from nova.api.openstack.compute import plugins
from nova.api.openstack.compute.plugins.v3 import security_groups
from nova.api.openstack.compute.plugins.v3 import servers
from nova.api.openstack import xmlutil
from nova import compute
from nova.compute import api as compute_api
from nova.compute import flavors
from nova.compute import power_state
from nova import db
from nova import exception
from nova.network import manager
from nova.objects import instance as instance_obj
from nova.openstack.common import jsonutils
from nova.openstack.common import rpc
from nova import test
from nova.tests.api.openstack import fakes
from nova.tests import fake_instance
import nova.tests.image.fake


CONF = cfg.CONF
FAKE_UUID = fakes.FAKE_UUID
FAKE_UUID1 = 'a47ae74e-ab08-447f-8eee-ffd43fc46c16'


def fake_gen_uuid():
    return FAKE_UUID


def return_security_group(context, instance_id, security_group_id):
    pass


def return_server(context, server_id, columns_to_join=None):
    return fake_instance.fake_db_instance(
        **{'id': int(server_id),
           'power_state': 0x01,
           'host': "localhost",
           'uuid': FAKE_UUID1,
           'name': 'asdf'})


def return_server_by_uuid(context, server_uuid, columns_to_join=None):
    return fake_instance.fake_db_instance(
        **{'id': 1,
           'power_state': 0x01,
           'host': "localhost",
           'uuid': server_uuid,
           'name': 'asdf'})


def return_non_running_server(context, server_id, columns_to_join=None):
    return fake_instance.fake_db_instance(
        **{'id': server_id, 'power_state': power_state.SHUTDOWN,
           'uuid': FAKE_UUID1, 'host': "localhost", 'name': 'asdf'})


def return_security_group_by_name(context, project_id, group_name):
    return {'id': 1, 'name': group_name,
            "instances": [{'id': 1, 'uuid': FAKE_UUID1}]}


def return_security_group_without_instances(context, project_id, group_name):
    return {'id': 1, 'name': group_name}


def return_server_nonexistent(context, server_id, columns_to_join=None):
    raise exception.InstanceNotFound(instance_id=server_id)


class TestSecurityGroups(test.TestCase):
    def setUp(self):
        super(TestSecurityGroups, self).setUp()
        self.manager = security_groups.SecurityGroupActionController()

    def test_associate_by_non_existing_security_group_name(self):
        self.stubs.Set(db, 'instance_get', return_server)
        self.assertEquals(return_server(None, '1'),
                          db.instance_get(None, '1'))
        body = dict(add_security_group=dict(name='non-existing'))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.manager._add_security_group, req, '1', body)

    def test_associate_by_invalid_server_id(self):
        body = dict(add_security_group=dict(name='test'))

        req = fakes.HTTPRequestV3.blank('/servers/invalid/action')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.manager._add_security_group,
                          req, 'invalid', body)

    def test_associate_without_body(self):
        self.stubs.Set(db, 'instance_get', return_server)
        body = dict(add_security_group=None)

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._add_security_group, req, '1', body)

    def test_associate_no_security_group_name(self):
        self.stubs.Set(db, 'instance_get', return_server)
        body = dict(add_security_group=dict())

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._add_security_group, req, '1', body)

    def test_associate_security_group_name_with_whitespaces(self):
        self.stubs.Set(db, 'instance_get', return_server)
        body = dict(add_security_group=dict(name="   "))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._add_security_group, req, '1', body)

    def test_associate_non_existing_instance(self):
        self.stubs.Set(db, 'instance_get', return_server_nonexistent)
        self.stubs.Set(db, 'instance_get_by_uuid',
                       return_server_nonexistent)
        body = dict(add_security_group=dict(name="test"))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.manager._add_security_group, req, '1', body)

    def test_associate_non_running_instance(self):
        self.stubs.Set(db, 'instance_get', return_non_running_server)
        self.stubs.Set(db, 'instance_get_by_uuid',
                       return_non_running_server)
        self.stubs.Set(db, 'security_group_get_by_name',
                       return_security_group_without_instances)
        body = dict(add_security_group=dict(name="test"))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.manager._add_security_group(req, '1', body)

    def test_associate_already_associated_security_group_to_instance(self):
        self.stubs.Set(db, 'instance_get', return_server)
        self.stubs.Set(db, 'instance_get_by_uuid',
                       return_server_by_uuid)
        self.stubs.Set(db, 'security_group_get_by_name',
                       return_security_group_by_name)
        body = dict(add_security_group=dict(name="test"))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._add_security_group, req, '1', body)

    def test_associate(self):
        self.stubs.Set(db, 'instance_get', return_server)
        self.stubs.Set(db, 'instance_get_by_uuid',
                       return_server_by_uuid)
        self.mox.StubOutWithMock(db, 'instance_add_security_group')
        db.instance_add_security_group(mox.IgnoreArg(),
                                            mox.IgnoreArg(),
                                            mox.IgnoreArg())
        self.stubs.Set(db, 'security_group_get_by_name',
                       return_security_group_without_instances)
        self.mox.ReplayAll()

        body = dict(add_security_group=dict(name="test"))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.manager._add_security_group(req, '1', body)

    def test_disassociate_by_non_existing_security_group_name(self):
        self.stubs.Set(db, 'instance_get', return_server)
        self.assertEquals(return_server(None, '1'),
                          db.instance_get(None, '1'))
        body = dict(remove_security_group=dict(name='non-existing'))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.manager._remove_security_group, req, '1', body)

    def test_disassociate_by_invalid_server_id(self):
        self.stubs.Set(db, 'security_group_get_by_name',
                       return_security_group_by_name)
        body = dict(remove_security_group=dict(name='test'))

        req = fakes.HTTPRequestV3.blank('/servers/invalid/action')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.manager._remove_security_group, req, 'invalid',
                          body)

    def test_disassociate_without_body(self):
        self.stubs.Set(db, 'instance_get', return_server)
        body = dict(remove_security_group=None)

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._remove_security_group, req, '1', body)

    def test_disassociate_no_security_group_name(self):
        self.stubs.Set(db, 'instance_get', return_server)
        body = dict(remove_security_group=dict())

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._remove_security_group, req, '1', body)

    def test_disassociate_security_group_name_with_whitespaces(self):
        self.stubs.Set(db, 'instance_get', return_server)
        body = dict(remove_security_group=dict(name="   "))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._remove_security_group, req, '1', body)

    def test_disassociate_non_existing_instance(self):
        self.stubs.Set(db, 'instance_get', return_server_nonexistent)
        self.stubs.Set(db, 'security_group_get_by_name',
                       return_security_group_by_name)
        body = dict(remove_security_group=dict(name="test"))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.manager._remove_security_group, req, '1', body)

    def test_disassociate_non_running_instance(self):
        self.stubs.Set(db, 'instance_get', return_non_running_server)
        self.stubs.Set(db, 'instance_get_by_uuid',
                       return_non_running_server)
        self.stubs.Set(db, 'security_group_get_by_name',
                       return_security_group_by_name)
        body = dict(remove_security_group=dict(name="test"))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.manager._remove_security_group(req, '1', body)

    def test_disassociate_already_associated_security_group_to_instance(self):
        self.stubs.Set(db, 'instance_get', return_server)
        self.stubs.Set(db, 'instance_get_by_uuid',
                       return_server_by_uuid)
        self.stubs.Set(db, 'security_group_get_by_name',
                       return_security_group_without_instances)
        body = dict(remove_security_group=dict(name="test"))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._remove_security_group, req, '1', body)

    def test_disassociate(self):
        self.stubs.Set(db, 'instance_get', return_server)
        self.stubs.Set(db, 'instance_get_by_uuid',
                       return_server_by_uuid)
        self.mox.StubOutWithMock(db, 'instance_remove_security_group')
        db.instance_remove_security_group(mox.IgnoreArg(),
                                    mox.IgnoreArg(),
                                    mox.IgnoreArg())
        self.stubs.Set(db, 'security_group_get_by_name',
                       return_security_group_by_name)
        self.mox.ReplayAll()

        body = dict(remove_security_group=dict(name="test"))

        req = fakes.HTTPRequestV3.blank('/servers/1/action')
        self.manager._remove_security_group(req, '1', body)


UUID1 = '00000000-0000-0000-0000-000000000001'
UUID2 = '00000000-0000-0000-0000-000000000002'
UUID3 = '00000000-0000-0000-0000-000000000003'


def fake_compute_get_all(*args, **kwargs):
    base = {'id': 1, 'description': 'foo', 'user_id': 'bar',
            'project_id': 'baz', 'deleted': False, 'deleted_at': None,
            'updated_at': None, 'created_at': None}
    db_list = [
        fakes.stub_instance(
            1, uuid=UUID1,
            security_groups=[dict(base, **{'name': 'fake-0-0'}),
                             dict(base, **{'name': 'fake-0-1'})]),
        fakes.stub_instance(
            2, uuid=UUID2,
            security_groups=[dict(base, **{'name': 'fake-1-0'}),
                             dict(base, **{'name': 'fake-1-1'})])
    ]

    return instance_obj._make_instance_list(args[1],
                                            instance_obj.InstanceList(),
                                            db_list,
                                            ['metadata', 'system_metadata',
                                             'security_groups', 'info_cache'])


def fake_compute_get(*args, **kwargs):
    return fakes.stub_instance(1, uuid=UUID3,
                               security_groups=[{'name': 'fake-2-0'},
                                                {'name': 'fake-2-1'}])


def fake_compute_create(*args, **kwargs):
    return ([fake_compute_get()], '')


def fake_get_instances_security_groups_bindings(inst, context):
    return {UUID1: [{'name': 'fake-0-0'}, {'name': 'fake-0-1'}],
            UUID2: [{'name': 'fake-1-0'}, {'name': 'fake-1-1'}]}


class SecurityGroupsOutputTest(test.TestCase):
    content_type = 'application/json'

    def setUp(self):
        super(SecurityGroupsOutputTest, self).setUp()
        fakes.stub_out_nw_api(self.stubs)
        self.stubs.Set(compute.api.API, 'get', fake_compute_get)
        self.stubs.Set(compute.api.API, 'get_all', fake_compute_get_all)
        self.stubs.Set(compute.api.API, 'create', fake_compute_create)
        self.app = fakes.wsgi_app_v3(init_only=('servers',
                                                'os-security-groups'))

    def _make_request(self, url, body=None):
        req = webob.Request.blank(url)
        if body:
            req.method = 'POST'
            req.body = self._encode_body(body)
        req.content_type = self.content_type
        req.headers['Accept'] = self.content_type
        res = req.get_response(self.app)
        return res

    def _encode_body(self, body):
        return jsonutils.dumps(body)

    def _get_server(self, body):
        return jsonutils.loads(body).get('server')

    def _get_servers(self, body):
        return jsonutils.loads(body).get('servers')

    def _get_groups(self, server):
        return server.get('security_groups')

    def test_create(self):
        url = '/v3/servers'
        image_uuid = 'c905cedb-7281-47e4-8a62-f26bc5fc4c77'
        server = dict(name='server_test', image_ref=image_uuid, flavor_ref=2)
        res = self._make_request(url, {'server': server})
        self.assertEqual(res.status_int, 202)
        server = self._get_server(res.body)
        for i, group in enumerate(self._get_groups(server)):
            name = 'fake-2-%s' % i
            self.assertEqual(group.get('name'), name)

    def test_show(self):
        url = '/v3/servers/%s' % UUID3
        res = self._make_request(url)

        self.assertEqual(res.status_int, 200)
        server = self._get_server(res.body)
        for i, group in enumerate(self._get_groups(server)):
            name = 'fake-2-%s' % i
            self.assertEqual(group.get('name'), name)

    def test_detail(self):
        url = '/v3/servers/detail'
        res = self._make_request(url)

        self.assertEqual(res.status_int, 200)
        for i, server in enumerate(self._get_servers(res.body)):
            for j, group in enumerate(self._get_groups(server)):
                name = 'fake-%s-%s' % (i, j)
                self.assertEqual(group.get('name'), name)

    def test_no_instance_passthrough_404(self):

        def fake_compute_get(*args, **kwargs):
            raise exception.InstanceNotFound(instance_id='fake')

        self.stubs.Set(compute.api.API, 'get', fake_compute_get)
        url = '/v3/servers/70f6db34-de8d-4fbd-aafb-4065bdfa6115'
        res = self._make_request(url)

        self.assertEqual(res.status_int, 404)


class SecurityGroupsOutputXmlTest(SecurityGroupsOutputTest):
    content_type = 'application/xml'

    class MinimalCreateServerTemplate(xmlutil.TemplateBuilder):
        def construct(self):
            root = xmlutil.TemplateElement('server', selector='server')
            root.set('name')
            root.set('id')
            root.set('image_ref')
            root.set('flavor_ref')
            return xmlutil.MasterTemplate(root, 1,
                                          nsmap={None: xmlutil.XMLNS_V11})

    def _encode_body(self, body):
        serializer = self.MinimalCreateServerTemplate()
        return serializer.serialize(body)

    def _get_server(self, body):
        return etree.XML(body)

    def _get_servers(self, body):
        return etree.XML(body).getchildren()

    def _get_groups(self, server):
        # NOTE(vish): we are adding security groups without an extension
        #             namespace so we don't break people using the existing
        #             functionality, but that means we need to use find with
        #             the existing server namespace.
        namespace = server.nsmap[None]
        return server.find('{%s}security_groups' % namespace).getchildren()


class ServersControllerCreateTest(test.TestCase):

    def setUp(self):
        """Shared implementation for tests below that create instance."""
        super(ServersControllerCreateTest, self).setUp()

        self.flags(verbose=True,
                   enable_instance_password=True)
        self.instance_cache_num = 0
        self.instance_cache_by_id = {}
        self.instance_cache_by_uuid = {}

        ext_info = plugins.LoadedExtensionInfo()
        self.controller = servers.ServersController(extension_info=ext_info)
        CONF.set_override('extensions_blacklist', 'os-security-groups',
                          'osapi_v3')
        self.no_security_groups_controller = servers.ServersController(
            extension_info=ext_info)

        def instance_create(context, inst):
            inst_type = flavors.get_flavor_by_flavor_id(3)
            image_uuid = '76fa36fc-c930-4bf3-8c8a-ea2a2420deb6'
            def_image_ref = 'http://localhost/images/%s' % image_uuid
            self.instance_cache_num += 1
            instance = fake_instance.fake_db_instance(**{
                'id': self.instance_cache_num,
                'display_name': inst['display_name'] or 'test',
                'uuid': FAKE_UUID,
                'instance_type': dict(inst_type),
                'access_ip_v4': '1.2.3.4',
                'access_ip_v6': 'fead::1234',
                'image_ref': inst.get('image_ref', def_image_ref),
                'user_id': 'fake',
                'project_id': 'fake',
                'reservation_id': inst['reservation_id'],
                "created_at": datetime.datetime(2010, 10, 10, 12, 0, 0),
                "updated_at": datetime.datetime(2010, 11, 11, 11, 0, 0),
                "user_data": None,
                "progress": 0,
                "fixed_ips": [],
                "task_state": "",
                "vm_state": "",
                "root_device_name": inst.get('root_device_name', 'vda'),
            })

            self.instance_cache_by_id[instance['id']] = instance
            self.instance_cache_by_uuid[instance['uuid']] = instance
            return instance

        def instance_get(context, instance_id):
            """Stub for compute/api create() pulling in instance after
            scheduling
            """
            return self.instance_cache_by_id[instance_id]

        def instance_update(context, uuid, values):
            instance = self.instance_cache_by_uuid[uuid]
            instance.update(values)
            return instance

        def server_update(context, instance_uuid, params):
            inst = self.instance_cache_by_uuid[instance_uuid]
            inst.update(params)
            return (inst, inst)

        def fake_method(*args, **kwargs):
            pass

        def project_get_networks(context, user_id):
            return dict(id='1', host='localhost')

        def queue_get_for(context, *args):
            return 'network_topic'

        fakes.stub_out_rate_limiting(self.stubs)
        fakes.stub_out_key_pair_funcs(self.stubs)
        nova.tests.image.fake.stub_out_image_service(self.stubs)
        fakes.stub_out_nw_api(self.stubs)
        self.stubs.Set(uuid, 'uuid4', fake_gen_uuid)
        self.stubs.Set(db, 'instance_add_security_group',
                       return_security_group)
        self.stubs.Set(db, 'project_get_networks',
                       project_get_networks)
        self.stubs.Set(db, 'instance_create', instance_create)
        self.stubs.Set(db, 'instance_system_metadata_update',
                       fake_method)
        self.stubs.Set(db, 'instance_get', instance_get)
        self.stubs.Set(db, 'instance_update', instance_update)
        self.stubs.Set(rpc, 'cast', fake_method)
        self.stubs.Set(db, 'instance_update_and_get_original',
                       server_update)
        self.stubs.Set(rpc, 'queue_get_for', queue_get_for)
        self.stubs.Set(manager.VlanManager, 'allocate_fixed_ip',
                       fake_method)

    def _test_create_extra(self, params, no_image=False,
                           override_controller=None):
        image_uuid = 'c905cedb-7281-47e4-8a62-f26bc5fc4c77'
        server = dict(name='server_test', image_ref=image_uuid, flavor_ref=2)
        if no_image:
            server.pop('image_ref', None)
        server.update(params)
        body = dict(server=server)
        req = fakes.HTTPRequestV3.blank('/servers')
        req.method = 'POST'
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"
        if override_controller:
            server = override_controller.create(req, body).obj['server']
        else:
            server = self.controller.create(req, body).obj['server']

    def test_create_instance_with_security_group_enabled(self):
        group = 'foo'
        old_create = compute_api.API.create

        def sec_group_get(ctx, proj, name):
            if name == group:
                return True
            else:
                raise exception.SecurityGroupNotFoundForProject(
                    project_id=proj, security_group_id=name)

        def create(*args, **kwargs):
            self.assertEqual(kwargs['security_group'], [group])
            return old_create(*args, **kwargs)

        self.stubs.Set(db, 'security_group_get_by_name', sec_group_get)
        # negative test
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self._test_create_extra,
                          {security_groups.ATTRIBUTE_NAME:
                           [{'name': 'bogus'}]})
        # positive test - extra assert in create path
        self.stubs.Set(compute_api.API, 'create', create)
        self._test_create_extra({security_groups.ATTRIBUTE_NAME:
                                 [{'name': group}]})

    def test_create_instance_with_security_group_disabled(self):
        group = 'foo'
        params = {'security_groups': [{'name': group}]}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            # NOTE(vish): if the security groups extension is not
            #             enabled, then security groups passed in
            #             are ignored.
            self.assertNotIn('security_group', kwargs)
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        self._test_create_extra(params,
            override_controller=self.no_security_groups_controller)


class TestServerCreateRequestXMLDeserializer(test.TestCase):

    def setUp(self):
        super(TestServerCreateRequestXMLDeserializer, self).setUp()
        ext_info = plugins.LoadedExtensionInfo()
        controller = servers.ServersController(extension_info=ext_info)
        self.deserializer = servers.CreateDeserializer(controller)

    def test_request_with_security_groups(self):
        serial_request = """
    <server xmlns="http://docs.openstack.org/compute/api/v3"
        name="security_groups_test"
        image_ref="1"
        flavor_ref="1">
    <os:security_groups xmlns:os="%(namespace)s">
       <security_group name="sg1"/>
       <security_group name="sg2"/>
    </os:security_groups>
    </server>""" % {
            'namespace': security_groups.SecurityGroups.namespace}
        request = self.deserializer.deserialize(serial_request)
        expected = {
            "server": {
            "name": "security_groups_test",
            "image_ref": "1",
            "flavor_ref": "1",
            security_groups.ATTRIBUTE_NAME: [{"name": "sg1"},
                                {"name": "sg2"}]
            },
        }
        self.assertEquals(request['body'], expected)
