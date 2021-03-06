import eventlet
import json
import sys
import requests
import os
import json

class KubeMonitor(object):

    def __init__(self, args=None, logger=None, q=None, db=None, beta=False):
        self.args = args
        self.logger = logger
        self.q = q
        self.cloud_orchestrator = os.getenv("CLOUD_ORCHESTRATOR")
        self.token = os.getenv("TOKEN") # valid only for OpenShift
        self.headers = {}

        # Use Kube DB if kube object caching is enabled in config.
        if args.kube_object_cache == 'True':
            self.db = db
        else:
            self.db = None

        if self.cloud_orchestrator == "openshift":
            protocol = "https"
            self.headers = {'Authorization': "Bearer " + self.token}
        else: # kubernetes
            protocol = "http"

        if beta:
            self.url = "%s://%s:%s/apis/extensions/v1beta1" % ( protocol,
                                              self.args.kubernetes_api_server, 
                                              self.args.kubernetes_api_port)
        else:
            self.url = "%s://%s:%s/api/v1" % (protocol,
                                              self.args.kubernetes_api_server,
                                              self.args.kubernetes_api_port)

        self.logger.info("KubeMonitor init done.");

    def register_monitor(self, resource_type):
        url = "%s/%s" % (self.url, resource_type)

        if self.cloud_orchestrator == "openshift":
            resp = requests.get(url, params={'watch': 'true'}, stream=True,
                                headers=self.headers, verify=False)
        else: # kubernetes
            resp = requests.get(url, params={'watch': 'true'}, stream=True)

        if resp.status_code != 200:
            return
        return resp.iter_lines(chunk_size=10, delimiter='\n')

    def get(self, resource_type, resource_name, namespace=None):
        if resource_type == "namespaces":
            url = "%s/%s" % (self.url, resource_type)
        else:
            url = "%s/namespaces/%s/%s/%s" % (self.url, namespace, 
                                              resource_type, resource_name)

        if self.cloud_orchestrator == "openshift":
            resp = requests.get(url, stream=True,
                                headers=self.headers, verify=False)
        else: # kubernetes
            resp = requests.get(url, stream=True)

        if resp.status_code != 200:
            return
        return resp.iter_lines(chunk_size=10, delimiter='\n')

    def patch(self, resource_type, resource_name, merge_patch, namespace=None):
        if resource_type == "namespaces":
            url = "%s/%s" % (self.url, resource_type)
        else:
            url = "%s/namespaces/%s/%s/%s" % (self.url, namespace, 
                                              resource_type, resource_name)

        self.headers.update({'Accept': 'application/json', 'Content-Type': 'application/strategic-merge-patch+json'})
        if self.cloud_orchestrator == "openshift":
            resp = requests.patch(url, headers=self.headers, data=json.dumps(merge_patch), verify=False)
        else: # kubernetes
            resp = requests.patch(url, headers=self.headers, data=json.dumps(merge_patch))

        if resp.status_code != 200:
            return
        return resp.iter_lines(chunk_size=10, delimiter='\n')
