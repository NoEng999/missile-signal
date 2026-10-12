import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import monitor

class ServerTests(unittest.TestCase):
    def test_env_overrides_secret_file_and_disables_x(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'secret.json'
            config.write_text('{"signal_account":"fake","signal_group_id":"fake-group","x_bearer_token":"dummy"}')
            with patch.dict(os.environ,{'SIGNAL_CONFIG_FILE':str(config),'SIGNAL_RPC':'http://signal:8080/api/v1/rpc'},clear=True):
                result=monitor.load_config()
            self.assertEqual(result['signal_group_id'],'fake-group')
            self.assertEqual(result['signal_rpc'],'http://signal:8080/api/v1/rpc')
            self.assertEqual(result['x_bearer_token'],'')
    def test_runner_rejects_missing_group_before_start(self):
        import runner
        with patch.dict(os.environ,{'SIGNAL_ACCOUNT':'fake'},clear=True):
            with self.assertRaises(SystemExit): runner.main()

if __name__=='__main__': unittest.main()
