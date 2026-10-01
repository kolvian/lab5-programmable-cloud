#!/usr/bin/env python3

import argparse
import os
import time
from urllib.error import URLError
from urllib.request import urlopen

import google.auth
import googleapiclient.discovery
from googleapiclient.errors import HttpError

ZONE = 'us-west1-b'
SCOPES = ['https://www.googleapis.com/auth/cloud-platform']
STARTUP_SCRIPT = '''#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-pip python3-venv git
mkdir -p /srv
cd /srv
if [ ! -d flask-tutorial ]; then
    git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial
fi
cd flask-tutorial
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install -e .
if [ ! -f instance/flaskr.sqlite ]; then
    FLASK_APP=flaskr .venv/bin/flask init-db
fi
cat > /etc/systemd/system/flaskr.service <<'SERVICE'
[Unit]
Description=Lab 5 Flask application
After=network.target
[Service]
WorkingDirectory=/srv/flask-tutorial
Environment=FLASK_APP=flaskr
ExecStart=/srv/flask-tutorial/.venv/bin/flask run --host=0.0.0.0 --port=5000
Restart=on-failure
[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable --now flaskr
'''


def client(project=None, credentials=None):
    if credentials is None:
        credentials, default_project = google.auth.default(scopes=SCOPES)
        project = project or os.getenv('GOOGLE_CLOUD_PROJECT') or default_project
    if not project:
        raise ValueError('Specify --project or GOOGLE_CLOUD_PROJECT.')
    return googleapiclient.discovery.build('compute', 'v1', credentials=credentials), project


def wait_operation(compute, project, operation, timeout=900):
    deadline = time.monotonic() + timeout
    while True:
        if operation['status'] == 'DONE':
            if 'error' in operation:
                raise RuntimeError(operation['error'])
            return operation
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Operation {operation['name']} timed out.")
        time.sleep(2)
        params = {'project': project, 'operation': operation['name']}
        if 'zone' in operation:
            resource = compute.zoneOperations()
            params['zone'] = operation['zone'].rsplit('/', 1)[-1]
        elif 'region' in operation:
            resource = compute.regionOperations()
            params['region'] = operation['region'].rsplit('/', 1)[-1]
        else:
            resource = compute.globalOperations()
        operation = resource.get(**params).execute()


def ensure_firewall(compute, project):
    try:
        rule = compute.firewalls().get(project=project, firewall='allow-5000').execute()
    except HttpError as error:
        if error.resp.status != 404:
            raise
        rule = None
    body = {
        'name': 'allow-5000', 'network': f'projects/{project}/global/networks/default',
        'direction': 'INGRESS', 'sourceRanges': ['0.0.0.0/0'],
        'targetTags': ['allow-5000'], 'allowed': [{'IPProtocol': 'tcp', 'ports': ['5000']}],
    }
    if rule is None:
        wait_operation(compute, project, compute.firewalls().insert(project=project, body=body).execute())
    elif (rule.get('targetTags') != body['targetTags']
          or rule.get('sourceRanges') != body['sourceRanges']
          or rule.get('allowed') != body['allowed'] or rule.get('disabled', False)
          or rule.get('direction') != 'INGRESS'
          or not rule.get('network', '').endswith(f'/projects/{project}/global/networks/default')):
        raise ValueError('Existing allow-5000 rule does not match the assignment configuration.')


def create_instance(compute, project, name, image=None, startup=STARTUP_SCRIPT, metadata=None, machine_type='f1-micro'):
    if image is None:
        image = compute.images().getFromFamily(
            project='ubuntu-os-cloud', family='ubuntu-2204-lts').execute()['selfLink']
    body = {
        'name': name, 'machineType': f'zones/{ZONE}/machineTypes/{machine_type}',
        'disks': [{'boot': True, 'autoDelete': True,
                   'initializeParams': {'sourceImage': image, 'diskSizeGb': '10'}}],
        'networkInterfaces': [{'network': f'projects/{project}/global/networks/default',
                               'accessConfigs': [{'name': 'External NAT', 'type': 'ONE_TO_ONE_NAT'}]}],
        'metadata': {'items': [{'key': 'startup-script', 'value': startup}] + (metadata or [])},
    }
    wait_operation(compute, project, compute.instances().insert(
        project=project, zone=ZONE, body=body).execute())
    instance = compute.instances().get(project=project, zone=ZONE, instance=name).execute()
    wait_operation(compute, project, compute.instances().setTags(
        project=project, zone=ZONE, instance=name,
        body={'items': ['allow-5000'], 'fingerprint': instance['tags']['fingerprint']}).execute())
    return instance_url(compute, project, name)


def instance_url(compute, project, name):
    instance = compute.instances().get(project=project, zone=ZONE, instance=name).execute()
    return f"http://{instance['networkInterfaces'][0]['accessConfigs'][0]['natIP']}:5000"


def wait_app(url, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url + '/hello', timeout=5) as response:
                if response.status == 200 and response.read() == b'Hello, World!':
                    with urlopen(url, timeout=5) as index:
                        if index.status == 200:
                            return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(5)
    raise TimeoutError(f'Flask did not become ready at {url}. Inspect VM startup logs.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project')
    parser.add_argument('--name', default='lab5-flask')
    parser.add_argument('--machine-type', default='f1-micro')
    args = parser.parse_args()
    compute, project = client(args.project)
    ensure_firewall(compute, project)
    url = create_instance(compute, project, args.name, machine_type=args.machine_type)
    print(f'Waiting for Flask at {url}', flush=True)
    wait_app(url)
    print(f'The Flask application is available at: {url}')


if __name__ == '__main__':
    main()
