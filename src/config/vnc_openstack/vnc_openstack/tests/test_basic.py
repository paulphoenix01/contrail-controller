import sys
import json

from testtools.matchers import Equals, Contains
from testtools import ExpectedException
import webtest.app

sys.path.append('../common/tests')
from test_utils import *
import test_common

import test_case

class TestBasic(test_case.NeutronBackendTestCase):
    def read_resource(self, url_pfx, id):
        context = {'operation': 'READ',
                   'user_id': '',
                   'roles': ''}
        data = {'fields': None,
                'id': id}
        body = {'context': context, 'data': data}
        resp = self._api_svr_app.post_json('/neutron/%s' %(url_pfx), body)
        return json.loads(resp.text)
    # end read_resource

    def list_resource(self, url_pfx,
        proj_uuid=None, req_fields=None, req_filters=None):
        if proj_uuid == None:
            proj_uuid = self._vnc_lib.fq_name_to_id('project',
            fq_name=['default-domain', 'default-project'])

        context = {'operation': 'READALL',
                   'user_id': '',
                   'tenant_id': proj_uuid,
                   'roles': '',
                   'is_admin': 'False'}
        data = {'fields': req_fields, 'filters': req_filters or {}}
        body = {'context': context, 'data': data}
        resp = self._api_svr_app.post_json(
            '/neutron/%s' %(url_pfx), body)
        return json.loads(resp.text)
    # end list_resource

    def test_list_with_inconsistent_members(self):
        # 1. create collection
        # 2. list, verify full collection
        # 3. mess with one in vnc_to_neutron, verify collection-1
        # 4. restore, list, verify full collection
        proj_obj = self._vnc_lib.project_read(
            fq_name=['default-domain', 'default-project'])

        objects = {}
        for (obj_type, obj_class, create_method_name) in \
            [('virtual_network', vnc_api.VirtualNetwork,
              'virtual_network_create'),
             ('network_ipam', vnc_api.NetworkIpam,
              'network_ipam_create'),
             ('network_policy', vnc_api.NetworkPolicy,
              'network_policy_create'),
             ('logical_router', vnc_api.LogicalRouter,
              'logical_router_create'),
             ('security_group', vnc_api.SecurityGroup,
              'security_group_create'),
             ('route_table', vnc_api.RouteTable,
              'route_table_create'),
             ('service_instance', vnc_api.ServiceInstance,
              'service_instance_create')]:
            objects[obj_type] = [obj_class('%s-%s' %(self.id(), i))
                                 for i in range(3)]
            for obj in objects[obj_type]:
                create_method = getattr(self._vnc_lib, create_method_name)
                create_method(obj)

        objects['virtual_machine_interface'] = \
            [vnc_api.VirtualMachineInterface('%s-%s' %(self.id(), i), proj_obj)
             for i in range(3)]
        for obj in objects['virtual_machine_interface']:
            obj.add_virtual_network(vnc_api.VirtualNetwork())
            self._vnc_lib.virtual_machine_interface_create(obj)

        vn_obj = vnc_api.VirtualNetwork(self.id())
        sn0_id = str(uuid.uuid4())
        sn1_id = str(uuid.uuid4())
        sn2_id = str(uuid.uuid4())
        vn_obj.add_network_ipam(vnc_api.NetworkIpam(),
            vnc_api.VnSubnetsType(
                [vnc_api.IpamSubnetType(vnc_api.SubnetType('1.1.1.0', 28),
                                        subnet_uuid=sn0_id),
                 vnc_api.IpamSubnetType(vnc_api.SubnetType('2.2.2.0', 28),
                                        subnet_uuid=sn1_id),
                 vnc_api.IpamSubnetType(vnc_api.SubnetType('3.3.3.0', 28),
                                        subnet_uuid=sn2_id)]))
        self._vnc_lib.virtual_network_create(vn_obj)

        fip_pool_obj = vnc_api.FloatingIpPool(self.id(), vn_obj)
        self._vnc_lib.floating_ip_pool_create(fip_pool_obj)
        objects['floating_ip'] = [vnc_api.FloatingIp('%s-%s' %(self.id(), i),
            fip_pool_obj)
            for i in range(3)]
        for obj in objects['floating_ip']:
            obj.add_project(proj_obj)
            self._vnc_lib.floating_ip_create(obj)

        collection_types = [
            (objects['virtual_network'], 'network',
             '_network_vnc_to_neutron'),
            (objects['virtual_machine_interface'], 'port',
             '_port_vnc_to_neutron'),
            (objects['network_ipam'], 'ipam',
             '_ipam_vnc_to_neutron'),
            (objects['network_policy'], 'policy',
             '_policy_vnc_to_neutron'),
            (objects['logical_router'], 'router',
             '_router_vnc_to_neutron'),
            (objects['floating_ip'], 'floatingip',
             '_floatingip_vnc_to_neutron'),
            (objects['security_group'], 'security_group',
             '_security_group_vnc_to_neutron'),
            (objects['route_table'], 'route_table',
             '_route_table_vnc_to_neutron'),
            (objects['service_instance'], 'nat_instance',
             '_svc_instance_vnc_to_neutron'),
        ]

        def list_resource(url_pfx):
            context = {'operation': 'READALL',
                       'user_id': '',
                       'tenant_id': proj_obj.uuid,
                       'roles': '',
                       'is_admin': 'False'}
            data = {'fields': None, 'filters': {}}
            body = {'context': context, 'data': data}
            resp = self._api_svr_app.post_json(
                '/neutron/%s' %(url_pfx), body)
            return json.loads(resp.text)

        # for collections that are objects in contrail model
        for (objects, res_url_pfx, res_xlate_name) in collection_types:
            res_dicts = list_resource(res_url_pfx)
            present_ids = [r['id'] for r in res_dicts] 
            for obj in objects:
                self.assertIn(obj.uuid, present_ids)

            neutron_api_obj = FakeExtensionManager.get_extension_objects(
                'vnc_cfg_api.neutronApi')[0]
            neutron_db_obj = neutron_api_obj._npi._cfgdb

            def err_on_object_2(orig_method, res_obj, *args, **kwargs):
                if res_obj.uuid == objects[2].uuid:
                    raise Exception('faking inconsistent element')
                return orig_method(res_obj, *args, **kwargs)

            with test_common.patch(
                neutron_db_obj, res_xlate_name, err_on_object_2):
                res_dicts = list_resource(res_url_pfx)
                present_ids = [r['id'] for r in res_dicts] 
                self.assertNotIn(objects[2].uuid, present_ids)

            res_dicts = list_resource(res_url_pfx)
            present_ids = [r['id'] for r in res_dicts] 
            for obj in objects:
                self.assertIn(obj.uuid, present_ids)
        # end for collections that are objects in contrail model

        # subnets, sg-rules etc.
        res_dicts = list_resource('subnet')
        present_ids = [r['id'] for r in res_dicts]
        for sn_id in [sn0_id, sn1_id, sn2_id]:
            self.assertIn(sn_id, present_ids)

        def err_on_sn2(orig_method, subnet_vnc, *args, **kwargs):
            if subnet_vnc.subnet_uuid == sn2_id:
                raise Exception('faking inconsistent element')
            return orig_method(subnet_vnc, *args, **kwargs)

        with test_common.patch(
            neutron_db_obj, '_subnet_vnc_to_neutron', err_on_sn2):
            res_dicts = list_resource('subnet')
            present_ids = [r['id'] for r in res_dicts] 
            self.assertNotIn(sn2_id, present_ids)
    # end test_list_with_inconsistent_members

    def test_subnet_uuid_heal(self):
        # 1. create 2 subnets thru vnc_api
        # 2. mess with useragent-kv index for one
        # 3. neutron subnet list should report fine
        # 4. neutron subnet list with uuids where
        #    one is a fake uuid shouldnt cause error in list
        ipam_obj = vnc_api.NetworkIpam('ipam-%s' %(self.id()))
        self._vnc_lib.network_ipam_create(ipam_obj)
        vn1_obj = vnc_api.VirtualNetwork('vn1-%s' %(self.id()))
        sn1_uuid = str(uuid.uuid4())
        vn1_obj.add_network_ipam(ipam_obj,
            vnc_api.VnSubnetsType(
                [vnc_api.IpamSubnetType(vnc_api.SubnetType('1.1.1.0', 28),
                                        subnet_uuid=sn1_uuid)]))
        self._vnc_lib.virtual_network_create(vn1_obj)
        vn2_obj = vnc_api.VirtualNetwork('vn2-%s' %(self.id()))
        sn2_uuid = str(uuid.uuid4())
        vn2_obj.add_network_ipam(ipam_obj,
            vnc_api.VnSubnetsType(
                [vnc_api.IpamSubnetType(vnc_api.SubnetType('2.2.2.0', 28),
                                        subnet_uuid=sn2_uuid)]))
        self._vnc_lib.virtual_network_create(vn2_obj)

        # the list primes cfgdb handle(conn to api server)
        self.list_resource('subnet')
        neutron_api_obj = FakeExtensionManager.get_extension_objects(
            'vnc_cfg_api.neutronApi')[0]
        neutron_db_obj = neutron_api_obj._npi._cfgdb

        heal_invoked = [False]
        def verify_heal_invoked(orig_method, *args, **kwargs):
            heal_invoked[0] = True
            return orig_method(*args, **kwargs)
        with test_common.patch(
            neutron_db_obj, 'subnet_id_heal', verify_heal_invoked):
            with CassandraCFs.get_cf(
                'useragent_keyval_table').patch_row(sn2_uuid, None):
                # verify heal
                rsp = self.list_resource('subnet',
                    req_filters={'id': [sn1_uuid, sn2_uuid]})
                self.assertEqual(heal_invoked[0], True)
                self.assertEqual(len(rsp), 2)
                self.assertEqual(set([r['id'] for r in rsp]),
                                 set([sn1_uuid, sn2_uuid]))

                # verify wrong/non-existent id doesn't cause
                # list to have error
                heal_invoked[0] = False
                fake_uuid = str(uuid.uuid4())
                rsp = self.list_resource('subnet',
                    req_filters={'id': [sn1_uuid, sn2_uuid, fake_uuid]})
                self.assertEqual(heal_invoked[0], True)
                self.assertEqual(len(rsp), 2)
                self.assertEqual(set([r['id'] for r in rsp]),
                                 set([sn1_uuid, sn2_uuid]))

                # verify read of non-existent id throws exception
                # and valid one doesn't
                with ExpectedException(webtest.app.AppError):
                    self.read_resource('subnet', fake_uuid)
                self.read_resource('subnet', sn1_uuid)
    # end test_subnet_uuid_heal

    def test_extra_fields_on_network(self):
        test_obj = self._create_test_object()
        context = {'operation': 'READ',
                   'user_id': '',
                   'roles': ''}
        data = {'fields': None,
                'id': test_obj.uuid}
        body = {'context': context, 'data': data}
        resp = self._api_svr_app.post_json('/neutron/network', body)
        net_dict = json.loads(resp.text)
        self.assertIn('contrail:fq_name', net_dict)
    # end test_extra_fields_on_network
# end class TestBasic

class TestExtraFieldsPresenceByKnob(test_case.NeutronBackendTestCase):
    def __init__(self, *args, **kwargs):
        super(TestExtraFieldsPresenceByKnob, self).__init__(*args, **kwargs)
        self._config_knobs.append(('NEUTRON', 'contrail_extensions_enabled', True))
    # end __init__

    def test_extra_fields_on_network(self):
        test_obj = self._create_test_object()
        context = {'operation': 'READ',
                   'user_id': '',
                   'roles': ''}
        data = {'fields': None,
                'id': test_obj.uuid}
        body = {'context': context, 'data': data}
        resp = self._api_svr_app.post_json('/neutron/network', body)
        net_dict = json.loads(resp.text)
        self.assertIn('contrail:fq_name', net_dict)
    # end test_extra_fields_on_network
# end class TestExtraFieldsPresenceByKnob

class TestExtraFieldsAbsenceByKnob(test_case.NeutronBackendTestCase):
    def __init__(self, *args, **kwargs):
        super(TestExtraFieldsAbsenceByKnob, self).__init__(*args, **kwargs)
        self._config_knobs.append(('NEUTRON', 'contrail_extensions_enabled', False))
    # end __init__

    def test_no_extra_fields_on_network(self):
        test_obj = self._create_test_object()
        context = {'operation': 'READ',
                   'user_id': '',
                   'roles': ''}
        data = {'fields': None,
                'id': test_obj.uuid}
        body = {'context': context, 'data': data}
        resp = self._api_svr_app.post_json('/neutron/network', body)
        net_dict = json.loads(resp.text)
        self.assertNotIn('contrail:fq_name', net_dict)
    # end test_extra_fields_on_network
# end class TestExtraFieldsAbsenceByKnob


class TestAuthenticatedAccess(test_case.NeutronBackendTestCase):
    test_obj_uuid = None
    test_failures = []
    expected_auth_token = ''
    auth_token_check_invocations = []
    def setUp(self):
        from keystonemiddleware import auth_token
        class FakeAuthProtocol(object):
            _test_case = self
            def __init__(self, app, *args, **kwargs):
                self._app = app
            # end __init__
            def __call__(self, env, start_response):
                # in multi-tenancy mode only admin role admitted
                # by api-server till full rbac support
                if (env['REQUEST_METHOD'] == 'GET' and
                    env['PATH_INFO'] == '/virtual-network/%s' %(
                        self._test_case.test_obj_uuid)):
                    # always execute but return back assertion
                    # errors
                    self._test_case.auth_token_check_invocations.append(True)
                    if not 'HTTP_X_AUTH_TOKEN' in env:
                        self._test_case.test_failures.append(
                            'Missing HTTP_X_AUTH_TOKEN')
                    if not env['HTTP_X_AUTH_TOKEN'].startswith(
                        self._test_case.expected_auth_token):
                        self._test_case.test_failures.append(
                            'Found wrong HTTP_X_AUTH_TOKEN %s' %(
                            env['HTTP_X_AUTH_TOKEN']))
                    env['HTTP_X_ROLE'] = 'admin'
                    return self._app(env, start_response)
                else:
                    env['HTTP_X_ROLE'] = 'admin'
                    return self._app(env, start_response)
            # end __call__
            def get_admin_token(self):
                return None
            # end get_admin_token
        # end class FakeAuthProtocol
        test_common.setup_extra_flexmock(
            [
            (auth_token, 'AuthProtocol', FakeAuthProtocol),
            ])
        self._config_knobs = [
                ('DEFAULTS', 'auth', 'keystone'),
                ('DEFAULTS', 'multi_tenancy', True),
                ('KEYSTONE', 'admin_user', 'foo'),
                ('KEYSTONE', 'admin_password', 'bar'),
                ('KEYSTONE', 'admin_tenant_name', 'baz'),
                ('KEYSTONE', 'auth_protocol', 'http'),
                ('KEYSTONE', 'auth_host', ''),
                ('KEYSTONE', 'auth_port', ''),
                ]
        super(TestAuthenticatedAccess, self).setUp()
    # end setUp

    def test_post_neutron_checks_auth_token(self):
        test_obj = self._create_test_object()
        TestAuthenticatedAccess.test_obj_uuid = test_obj.uuid
        context = {'operation': 'READ',
                   'user_id': '',
                   'roles': ''}
        data = {'fields': None,
                'id': test_obj.uuid}
        body = {'context': context, 'data': data}
        TestAuthenticatedAccess.expected_auth_token = 'no user token for'
        self._api_svr_app.post_json('/neutron/network', body)
        self.assertEqual(len(self.auth_token_check_invocations), 1)
        self.assertEqual(self.test_failures, [])

        TestAuthenticatedAccess.expected_auth_token = 'abc123'
        self._api_svr_app.post_json('/neutron/network', body,
            headers={'X_AUTH_TOKEN':'abc123'})
        self.assertEqual(len(self.auth_token_check_invocations), 2)
        self.assertEqual(self.test_failures, [])
    # end test_post_neutron_checks_auth_token
# end class TestAuthenticatedAccess
