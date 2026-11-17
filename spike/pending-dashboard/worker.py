"""UNSAFE synthetic local fixture, not an application or production service."""
import json
import bank
from js import JSON
from hosted.object import _rpc_result
from synthetic_recovery_socket import SyntheticRecoverySocket
from dashboard_contract import cfg

class PendingDashboard(SyntheticRecoverySocket):
    async def fixture_call(self, method, args):
        methods = {'setup': self.fixture_setup, 'grant': self.fixture_grant_owner,
                   'mutate': self.mutate, 'acknowledge': self.acknowledge,
                   'fault': self.fixture_fault, 'full_snapshot': self.fixture_full_snapshot}
        try:
            value = await methods[method](*args)
            return _rpc_result({'ok': True, 'value': json.loads(JSON.stringify(value))})
        except Exception as e:
            categories = {(ValueError, 'unacknowledged operation'): 'pending',
                          (ValueError, 'result mismatch'): 'result_mismatch',
                          (RuntimeError, 'synthetic injected fault'): 'injected_fault',
                          (PermissionError, 'denied'): 'denied'}
            return _rpc_result({'ok': False, 'error': categories.get((type(e), str(e)), 'other')})

    async def dashboard_read(self, family, token, kind, kid_id=0):
        try:
            a = self.auth
            def read(c):
                a._auth(c, family, token)
                revision = a._revision(c)
                if kind == 'state':
                    return {'kids': a.bank._read(c, 'kids', []), 'config': cfg(c),
                            'server_now': bank.now_ts(c), 'revision': revision}
                if kind == 'transactions':
                    return {'transactions': a.bank._read(c, 'transactions', [kid_id]),
                            'server_now': bank.now_ts(c), 'revision': revision}
                raise ValueError('unsupported read')
            if kind == 'pending':
                value = a.pending(family, token)
            else:
                value = a.bank._transaction(read)
            return _rpc_result({'ok': True, 'value': value})
        except PermissionError:
            return _rpc_result({'ok': False, 'denied': True})
        except Exception:
            return _rpc_result({'ok': False, 'denied': False})
