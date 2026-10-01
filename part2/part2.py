#!/usr/bin/env python3

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'part1'))
from part1 import (
    ZONE,
    client,
    create_instance,
    ensure_firewall,
    wait_app,
    wait_operation,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project')
    parser.add_argument('--source', default='lab5-flask')
    parser.add_argument('--prefix', default='lab5-clone')
    parser.add_argument('--machine-type', default='f1-micro')
    args = parser.parse_args()
    compute, project = client(args.project)
    ensure_firewall(compute, project)
    source = compute.instances().get(project=project, zone=ZONE, instance=args.source).execute()
    disk = next(d['source'].rsplit('/', 1)[-1] for d in source['disks'] if d['boot'])
    snapshot = f'base-snapshot-{args.source}'
    was_running = source['status'] == 'RUNNING'
    if was_running:
        wait_operation(compute, project, compute.instances().stop(
            project=project, zone=ZONE, instance=args.source).execute())
    try:
        wait_operation(compute, project, compute.disks().createSnapshot(
            project=project, zone=ZONE, disk=disk, body={'name': snapshot}).execute())
    finally:
        if was_running:
            wait_operation(compute, project, compute.instances().start(
                project=project, zone=ZONE, instance=args.source).execute())
    image_name = f'base-image-{args.source}'
    wait_operation(compute, project, compute.images().insert(project=project, body={
        'name': image_name, 'sourceSnapshot': f'projects/{project}/global/snapshots/{snapshot}',
    }).execute())
    image = f'projects/{project}/global/images/{image_name}'
    output = Path(__file__).with_name('TIMING.md')
    output.write_text(
        f'# Clone creation timing\n\nMeasured at {datetime.now(timezone.utc).isoformat()}.\n\n'
        f'Project: `{project}`. Zone: `{ZONE}`. Machine type: `{args.machine_type}`.\n\n'
        f'Source disk: `{disk}`. Snapshot: `{snapshot}`. Image: `{image_name}`.\n\n'
        'Creation time measures the insert request through completed creation and network tagging. '
        'Application readiness measures the same start through successful Flask HTTP checks.\n\n')
    for i in range(1, 4):
        name = f'{args.prefix}-{i}'
        start = time.perf_counter()
        url = create_instance(compute, project, name, image=image, startup='#!/bin/bash\nset -e\nsystemctl start flaskr\n', machine_type=args.machine_type)
        creation = time.perf_counter() - start
        wait_app(url)
        ready = time.perf_counter() - start
        result = f'- `{name}`: creation {creation:.2f} seconds; application ready {ready:.2f} seconds.\n'
        with output.open('a') as file:
            file.write(result)
        print(result.strip(), url, flush=True)


if __name__ == '__main__':
    main()
