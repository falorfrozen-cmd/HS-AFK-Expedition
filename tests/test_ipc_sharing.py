"""Windows output-handle race must not cause duplicate game commands."""
import sys,tempfile,unittest,threading,time
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from afk import Ipc


class IpcSharingTests(unittest.TestCase):
    def test_pending_command_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            ipc=Ipc(Path(temp));ipc.cmd.write_text('existing command',encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'pending'):
                ipc.send('new command',timeout=.06)
            self.assertEqual(ipc.cmd.read_text(encoding='utf-8'),'existing command')

    def test_two_writers_receive_their_own_reply_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);ipc=Ipc(root);seen=[];results={};errors=[];stop=threading.Event()
            def game():
                while not stop.wait(.01):
                    if ipc.cmd.exists():
                        command=ipc.cmd.read_text(encoding='utf-8').strip()
                        ipc.cmd.unlink();seen.append(command)
                        time.sleep(.1)
                        ipc.out.write_text(command+'\n---- done ----\n',encoding='utf-8')
            def writer(command):
                try:results[command]=Ipc(root).send(command,timeout=3)
                except Exception as error:errors.append(error)
            consumer=threading.Thread(target=game);consumer.start()
            try:
                writers=[threading.Thread(target=writer,args=(c,)) for c in ('one','two')]
                for thread in writers:thread.start()
                for thread in writers:thread.join(5)
                self.assertFalse(any(t.is_alive() for t in writers));self.assertEqual(errors,[])
                self.assertCountEqual(seen,['one','two']);self.assertEqual(results,{'one':['one'],'two':['two']})
            finally:stop.set();consumer.join(2)

    def test_sharing_violation_waits_before_publishing_once(self):
        with tempfile.TemporaryDirectory() as temp:
            ipc=Ipc(Path(temp));error=PermissionError('sharing violation');error.winerror=32
            ipc.out=Mock();ipc.cmd=Mock();ipc.cmd.exists.return_value=False
            ipc.out.unlink.side_effect=[error,None]
            ipc.out.read_text.side_effect=[error,'ok\n---- done ----\n']
            with patch('afk.time.sleep'):
                self.assertEqual(ipc._send_locked('afk status'),['ok'])
            self.assertEqual(ipc.out.unlink.call_count,2)
            ipc.cmd.write_text.assert_called_once_with('afk status\n',encoding='utf-8')

    def test_locked_output_timeout_never_publishes_command(self):
        with tempfile.TemporaryDirectory() as temp:
            ipc=Ipc(Path(temp));error=PermissionError('sharing violation');error.winerror=32
            ipc.out=Mock();ipc.cmd=Mock();ipc.cmd.exists.return_value=False;ipc.out.unlink.side_effect=error
            with patch('afk.time.monotonic',side_effect=[0,2]):
                self.assertIsNone(ipc._send_locked('afk status',timeout=1))
            ipc.cmd.write_text.assert_not_called()

    def test_other_permission_errors_are_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            ipc=Ipc(Path(temp));ipc.out=Mock();ipc.cmd=Mock();ipc.cmd.exists.return_value=False;ipc.out.unlink.side_effect=PermissionError('denied')
            with self.assertRaises(PermissionError):ipc._send_locked('afk status')
            ipc.cmd.write_text.assert_not_called()
