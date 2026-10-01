import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('part1', Path(__file__).parent / 'part1/part1.py')
part1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(part1)


def check():
    compute = Mock()
    compute.zoneOperations().get().execute.side_effect = [
        {'name': 'op', 'status': 'RUNNING', 'zone': 'zones/us-west1-b'},
        {'name': 'op', 'status': 'DONE'},
    ]
    with patch.object(part1.time, 'sleep'):
        part1.wait_operation(compute, 'project', {
            'name': 'op', 'status': 'PENDING', 'zone': 'zones/us-west1-b'})
    assert compute.zoneOperations().get.call_args.kwargs == {
        'project': 'project', 'zone': 'us-west1-b', 'operation': 'op'}
    try:
        part1.wait_operation(compute, 'project', {'name': 'bad', 'status': 'DONE', 'error': {'errors': ['failed']}})
    except RuntimeError:
        pass
    else:
        raise AssertionError('Operation failures must propagate.')
    compute = Mock()
    compute.instances().insert().execute.return_value = {'name': 'op', 'status': 'DONE'}
    compute.instances().setTags().execute.return_value = {'name': 'tags', 'status': 'DONE'}
    compute.instances().get().execute.return_value = {
        'tags': {'fingerprint': 'fingerprint'},
        'networkInterfaces': [{'accessConfigs': [{'natIP': '192.0.2.1'}]}],
    }
    assert part1.create_instance(compute, 'project', 'vm', image='image') == 'http://192.0.2.1:5000'
    body = compute.instances().insert.call_args.kwargs['body']
    assert body['machineType'] == 'zones/us-west1-b/machineTypes/f1-micro'
    assert body['disks'][0]['initializeParams']['sourceImage'] == 'image'
    assert body['networkInterfaces'][0]['accessConfigs'][0]['type'] == 'ONE_TO_ONE_NAT'
    assert compute.instances().setTags.call_args.kwargs['body'] == {
        'items': ['allow-5000'], 'fingerprint': 'fingerprint'}
    compute.firewalls().get().execute.return_value = {
        'network': 'https://compute.googleapis.com/compute/v1/projects/project/global/networks/default',
        'targetTags': ['allow-5000'], 'sourceRanges': ['0.0.0.0/0'],
        'direction': 'INGRESS', 'allowed': [{'IPProtocol': 'tcp', 'ports': ['5000']}],
    }
    part1.ensure_firewall(compute, 'project')
    compute.firewalls().insert.assert_not_called()
    compute.firewalls().get().execute.return_value['disabled'] = True
    try:
        part1.ensure_firewall(compute, 'project')
    except ValueError:
        pass
    else:
        raise AssertionError('A disabled existing rule must be rejected.')
    from googleapiclient.errors import HttpError
    response = Mock(status=404, reason='Not Found')
    compute.firewalls().get().execute.side_effect = HttpError(response, b'{"error":{"message":"missing"}}')
    compute.firewalls().insert().execute.return_value = {'name': 'firewall', 'status': 'DONE'}
    part1.ensure_firewall(compute, 'project')
    rule = compute.firewalls().insert.call_args.kwargs['body']
    assert rule['targetTags'] == ['allow-5000']
    assert rule['allowed'] == [{'IPProtocol': 'tcp', 'ports': ['5000']}]
    with patch.object(part1.time, 'monotonic', side_effect=[0, 1]):
        try:
            part1.wait_app('http://192.0.2.1:5000', timeout=0)
        except TimeoutError:
            pass
        else:
            raise AssertionError('Application timeout must propagate.')
    print('Local operation, VM configuration, firewall, and timeout checks passed.')


if __name__ == '__main__':
    check()
