# Copyright 2012 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from unittest import mock
import urllib.parse

import fixtures
from oslo_serialization import jsonutils
import requests
from requests_mock.contrib import fixture as rm_fixture
from testtools import matchers
import webob

from keystonemiddleware import s3_token
from keystonemiddleware.tests.unit import utils


GOOD_RESPONSE = {'access': {'token': {'id': 'TOKEN_ID',
                                      'tenant': {'id': 'TENANT_ID'}}}}


class FakeApp(object):
    """This represents a WSGI app protected by the auth_token middleware."""

    def __call__(self, env, start_response):
        resp = webob.Response()
        resp.environ = env
        return resp(env, start_response)


class S3TokenMiddlewareTestBase(utils.TestCase):

    TEST_WWW_AUTHENTICATE_URI = 'https://fakehost/identity'
    TEST_URL = '%s/v3/s3tokens' % (TEST_WWW_AUTHENTICATE_URI, )

    def setUp(self):
        super(S3TokenMiddlewareTestBase, self).setUp()

        self.conf = {
            'www_authenticate_uri': self.TEST_WWW_AUTHENTICATE_URI,
        }

        self.requests_mock = self.useFixture(rm_fixture.Fixture())

    def start_fake_response(self, status, headers):
        self.response_status = int(status.split(' ', 1)[0])
        self.response_headers = dict(headers)


class S3TokenMiddlewareTestGood(S3TokenMiddlewareTestBase):

    def setUp(self):
        super(S3TokenMiddlewareTestGood, self).setUp()
        self.middleware = s3_token.S3Token(FakeApp(), self.conf)

        self.requests_mock.post(self.TEST_URL,
                                status_code=201,
                                json=GOOD_RESPONSE)

    # Ignore the request and pass to the next middleware in the
    # pipeline if no path has been specified.
    def test_no_path_request(self):
        req = webob.Request.blank('/')
        self.middleware(req.environ, self.start_fake_response)
        self.assertEqual(self.response_status, 200)

    # Ignore the request and pass to the next middleware in the
    # pipeline if no Authorization header has been specified
    def test_without_authorization(self):
        req = webob.Request.blank('/v1/AUTH_cfa/c/o')
        self.middleware(req.environ, self.start_fake_response)
        self.assertEqual(self.response_status, 200)

    def test_without_auth_storage_token(self):
        req = webob.Request.blank('/v1/AUTH_cfa/c/o')
        req.headers['Authorization'] = 'badboy'
        self.middleware(req.environ, self.start_fake_response)
        self.assertEqual(self.response_status, 200)

    def test_authorized(self):
        req = webob.Request.blank('/v1/AUTH_cfa/c/o')
        req.headers['Authorization'] = 'access:signature'
        req.headers['X-Storage-Token'] = 'token'
        req.get_response(self.middleware)
        self.assertTrue(req.path.startswith('/v1/AUTH_TENANT_ID'))
        self.assertEqual(req.headers['X-Auth-Token'], 'TOKEN_ID')

    def test_authorized_http(self):
        protocol = 'http'
        host = 'fakehost'
        port = 35357
        self.requests_mock.post(
            '%s://%s:%s/v3/s3tokens' % (protocol, host, port),
            status_code=201, json=GOOD_RESPONSE)

        self.middleware = (
            s3_token.filter_factory({'auth_protocol': protocol,
                                     'auth_host': host,
                                     'auth_port': port})(FakeApp()))
        req = webob.Request.blank('/v1/AUTH_cfa/c/o')
        req.headers['Authorization'] = 'access:signature'
        req.headers['X-Storage-Token'] = 'token'
        req.get_response(self.middleware)
        self.assertTrue(req.path.startswith('/v1/AUTH_TENANT_ID'))
        self.assertEqual(req.headers['X-Auth-Token'], 'TOKEN_ID')

    def test_authorization_nova_toconnect(self):
        req = webob.Request.blank('/v1/AUTH_swiftint/c/o')
        req.headers['Authorization'] = 'access:FORCED_TENANT_ID:signature'
        req.headers['X-Storage-Token'] = 'token'
        req.get_response(self.middleware)
        path = req.environ['PATH_INFO']
        self.assertTrue(path.startswith('/v1/AUTH_FORCED_TENANT_ID'))

    @mock.patch.object(requests, 'post')
    def test_insecure(self, MOCK_REQUEST):
        self.middleware = (
            s3_token.filter_factory({'insecure': 'True'})(FakeApp()))

        text_return_value = jsonutils.dumps(GOOD_RESPONSE).encode()
        MOCK_REQUEST.return_value = utils.TestResponse({
            'status_code': 201,
            'text': text_return_value})

        req = webob.Request.blank('/v1/AUTH_cfa/c/o')
        req.headers['Authorization'] = 'access:signature'
        req.headers['X-Storage-Token'] = 'token'
        req.get_response(self.middleware)

        self.assertTrue(MOCK_REQUEST.called)
        mock_args, mock_kwargs = MOCK_REQUEST.call_args
        self.assertIs(mock_kwargs['verify'], False)

    def test_insecure_option(self):
        # insecure is passed as a string.

        # Some non-secure values.
        true_values = ['true', 'True', '1', 'yes']
        for val in true_values:
            config = {'insecure': val, 'certfile': 'false_ind'}
            middleware = s3_token.filter_factory(config)(FakeApp())
            self.assertIs(False, middleware._verify)

        # Some "secure" values, including unexpected value.
        false_values = ['false', 'False', '0', 'no', 'someweirdvalue']
        for val in false_values:
            config = {'insecure': val, 'certfile': 'false_ind'}
            middleware = s3_token.filter_factory(config)(FakeApp())
            self.assertEqual('false_ind', middleware._verify)

        # Default is secure.
        config = {'certfile': 'false_ind'}
        middleware = s3_token.filter_factory(config)(FakeApp())
        self.assertIs('false_ind', middleware._verify)

    def test_unicode_path(self):
        url = u'/v1/AUTH_cfa/c/euro\u20ac'.encode('utf8')
        req = webob.Request.blank(urllib.parse.quote(url))
        req.headers['Authorization'] = 'access:signature'
        req.headers['X-Storage-Token'] = 'token'
        req.get_response(self.middleware)


class S3TokenMiddlewareTestBad(S3TokenMiddlewareTestBase):
    def setUp(self):
        super(S3TokenMiddlewareTestBad, self).setUp()
        self.middleware = s3_token.S3Token(FakeApp(), self.conf)

    def test_unauthorized_token(self):
        ret = {"error":
               {"message": "EC2 access key not found.",
                "code": 401,
                "title": "Unauthorized"}}
        self.requests_mock.post(self.TEST_URL, status_code=403, json=ret)
        req = webob.Request.blank('/v1/AUTH_cfa/c/o')
        req.headers['Authorization'] = 'access:signature'
        req.headers['X-Storage-Token'] = 'token'
        resp = req.get_response(self.middleware)
        s3_denied_req = self.middleware._deny_request('AccessDenied')
        self.assertEqual(resp.body, s3_denied_req.body)
        self.assertEqual(resp.status_int, s3_denied_req.status_int)

    def test_bogus_authorization(self):
        req = webob.Request.blank('/v1/AUTH_cfa/c/o')
        req.headers['Authorization'] = 'badboy'
        req.headers['X-Storage-Token'] = 'token'
        resp = req.get_response(self.middleware)
        self.assertEqual(resp.status_int, 400)
        s3_invalid_req = self.middleware._deny_request('InvalidURI')
        self.assertEqual(resp.body, s3_invalid_req.body)
        self.assertEqual(resp.status_int, s3_invalid_req.status_int)

    def test_fail_to_connect_to_keystone(self):
        with mock.patch.object(self.middleware, '_json_request') as o:
            s3_invalid_req = self.middleware._deny_request('InvalidURI')
            o.side_effect = s3_token.ServiceError(s3_invalid_req)

            req = webob.Request.blank('/v1/AUTH_cfa/c/o')
            req.headers['Authorization'] = 'access:signature'
            req.headers['X-Storage-Token'] = 'token'
            resp = req.get_response(self.middleware)
            self.assertEqual(resp.body, s3_invalid_req.body)
            self.assertEqual(resp.status_int, s3_invalid_req.status_int)

    def test_bad_reply(self):
        self.requests_mock.post(self.TEST_URL,
                                status_code=201,
                                text="<badreply>")

        req = webob.Request.blank('/v1/AUTH_cfa/c/o')
        req.headers['Authorization'] = 'access:signature'
        req.headers['X-Storage-Token'] = 'token'
        resp = req.get_response(self.middleware)
        s3_invalid_req = self.middleware._deny_request('InvalidURI')
        self.assertEqual(resp.body, s3_invalid_req.body)
        self.assertEqual(resp.status_int, s3_invalid_req.status_int)


class S3TokenMiddlewareTestDeprecatedOptions(S3TokenMiddlewareTestBase):
    def setUp(self):
        super(S3TokenMiddlewareTestDeprecatedOptions, self).setUp()
        self.conf = {
            'auth_uri': self.TEST_WWW_AUTHENTICATE_URI,
        }
        self.logger = self.useFixture(fixtures.FakeLogger())
        self.middleware = s3_token.S3Token(FakeApp(), self.conf)

        self.requests_mock.post(self.TEST_URL,
                                status_code=201,
                                json=GOOD_RESPONSE)

    def test_logs_warning(self):
        req = webob.Request.blank('/')
        self.middleware(req.environ, self.start_fake_response)
        self.assertEqual(self.response_status, 200)
        log = "Use of the auth_uri option was deprecated in the Queens " \
            "release in favor of www_authenticate_uri."
        self.assertThat(self.logger.output, matchers.Contains(log))
