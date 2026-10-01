#!/usr/bin/env python3

import argparse
import json
import shlex
import sys
from pathlib import Path

from google.oauth2 import service_account

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'part1'))
from part1 import (
    SCOPES,
    client,
    create_instance,
    ensure_firewall,
    instance_url,
    wait_app,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project')
    parser.add_argument('--credentials', default='service-credentials.json')
    parser.add_argument('--name', default='lab5-launcher')
    parser.add_argument('--child', default='lab5-child')
    parser.add_argument('--machine-type', default='f1-micro')
    args = parser.parse_args()
    key_file = Path(args.credentials)
    credentials = service_account.Credentials.from_service_account_file(str(key_file), scopes=SCOPES)
    compute, project = client(args.project or json.loads(key_file.read_text())['project_id'], credentials)
    ensure_firewall(compute, project)
    launcher = '''from google.oauth2 import service_account
from part1 import SCOPES, client, create_instance, wait_app
import os
credentials = service_account.Credentials.from_service_account_file('/srv/lab5/service-credentials.json', scopes=SCOPES)
compute, project = client(os.environ['GOOGLE_CLOUD_PROJECT'], credentials)
url = create_instance(compute, project, os.environ['LAB5_CHILD'], machine_type=os.environ['LAB5_MACHINE_TYPE'])
wait_app(url)
print('The Flask application is available at: ' + url, flush=True)
'''
    startup = '''#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv curl
install -d -m 700 /srv/lab5
cd /srv/lab5
umask 077
for item in service-credentials part1-code launcher-code; do
    curl -fsS "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$item" \\
        -H 'Metadata-Flavor: Google' > "$item"
done
mv service-credentials service-credentials.json
mv part1-code part1.py
mv launcher-code launcher.py
python3 -m venv .venv
.venv/bin/pip install google-api-python-client google-auth
'''
    startup += f'export GOOGLE_CLOUD_PROJECT={shlex.quote(project)}\n'
    startup += f'export LAB5_CHILD={shlex.quote(args.child)}\n'
    startup += f'export LAB5_MACHINE_TYPE={shlex.quote(args.machine_type)}\n'
    startup += '.venv/bin/python -u launcher.py > /srv/lab5/launcher.log 2>&1\n'
    metadata = [
        {'key': 'service-credentials', 'value': key_file.read_text()},
        {'key': 'part1-code', 'value': Path(sys.path[0], 'part1.py').read_text()},
        {'key': 'launcher-code', 'value': launcher},
    ]
    create_instance(compute, project, args.name, startup=startup, metadata=metadata, machine_type=args.machine_type)
    print(f'{args.name} is launching {args.child}.', flush=True)
    import time

    from googleapiclient.errors import HttpError
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        try:
            url = instance_url(compute, project, args.child)
            break
        except HttpError as error:
            if error.resp.status != 404:
                raise
        except (KeyError, IndexError):
            pass
        time.sleep(5)
    else:
        raise TimeoutError('The launcher did not create the child. Inspect /srv/lab5/launcher.log.')
    wait_app(url)
    print(f'The Flask application created by {args.name} is available at: {url}')


if __name__ == '__main__':
    main()
