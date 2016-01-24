import asyncio
import struct
import logging
import pybtracker
import asynctest
from ipaddress import ip_address
from datetime import datetime, timedelta
from asynctest.mock import patch, Mock, CoroutineMock

logger = logging.getLogger(__name__)

class TrackerServerProtocolTest(asynctest.TestCase):
    async def setUp(self):
        self.server = Mock(autospec=pybtracker.TrackerServer)
        self.server.logger = logger
        self.server.connids = {}
        self.server.activity = {}
        self.server.connid_valid_period = 100
        self.server.interval = 200
        self.server.torrents = {
            b'01234567890123456789': {
                b'11111111111111111111': ('1.1.1.1', 1111, 0, 0, 0, False),
                b'22222222222222222222': ('2.2.2.2', 2222, 0, 0, 0, False),
                b'33333333333333333333': ('3.3.3.3', 3333, 0, 0, 0, False),
            },
            b'ABCDEFGHIJKLMNOPQRST': {
                b'11111111111111111111': ('1.1.1.1', 1111, 0, 0, 0, False),
                b'44444444444444444444': ('4.4.4.4', 4444, 0, 0, 0, False),
            }
        }
        self.proto = pybtracker.server.UdpTrackerServerProto(self.server)
        self.proto.transport = Mock(asyncio.DatagramTransport)

    def assert_is_error(self, msg, expected_tid, expected_msg):
        action, tid = struct.unpack('!II', msg[:8])
        error_msg = msg[8:]
        self.assertEqual(action, 3)
        self.assertEqual(tid, expected_tid)
        self.assertEqual(error_msg, expected_msg.encode())

    def create_announce_payload(self, infohash, peerid, downloaded,
                                left, uploaded, event, ip, key,
                                num_want, port, ext):
        ip = int.from_bytes(ip_address(ip).packed, byteorder='big')
        return struct.pack(
            '!20s20sQQQIIIIHH', infohash, peerid, downloaded, left,
            uploaded, event, ip, key, num_want, port, ext)

    async def test_process_connect_invalid(self):
        result = self.proto.process_connect(
            ('127.0.0.1', 8007),
            0x4444444444,
            1000,
            b'')
        self.assertGreaterEqual(len(result), 16)
        self.assert_is_error(result, 1000, 'Invalid protocol identifier.')

    @patch('pybtracker.server.datetime', autospec=pybtracker.server.datetime)
    async def test_process_connect_valid(self, mock_datetime):
        timestamp = datetime(1854, 1, 6, 5, 23, 30, 400)
        mock_datetime.now.return_value = timestamp
        result = self.proto.process_connect(
            ('127.0.0.1', 8007),
            0x41727101980,
            1000,
            b'')
        self.assertEqual(len(result), 16)
        self.assertEqual(result[:8], struct.pack('!II', 0, 1000))

        connid = int.from_bytes(result[8:], byteorder='big')
        self.assertIn(connid, self.server.connids)
        self.assertEqual(self.server.connids[connid], timestamp)

    async def test_process_announce_invalid_connid(self):
        self.server.connids[0x88776655] = datetime.now()
        result = self.proto.process_announce(
            ('127.0.0.1', 8007),
            0x99887766,
            1000,
            self.create_announce_payload(
                infohash=b'01234567890123456789',
                peerid=b'98765432109876543210',
                downloaded=1000,
                left=2000,
                uploaded=500,
                event=0,
                ip='127.0.0.2',
                key=0,
                num_want=100,
                port=8009,
                ext=0))
        self.assert_is_error(result, 1000, 'Invalid connection identifier.')

    async def test_process_announce_old_connid(self):
        self.server.connids[0x99887766] = datetime.now() - \
                                          timedelta(seconds=self.server.connid_valid_period + 1)
        result = self.proto.process_announce(
            ('127.0.0.1', 8007),
            0x99887766,
            1000,
            self.create_announce_payload(
                infohash=b'01234567890123456789',
                peerid=b'98765432109876543210',
                downloaded=1000,
                left=2000,
                uploaded=500,
                event=0,
                ip='127.0.0.2',
                key=0,
                num_want=100,
                port=8009,
                ext=0))
        self.assert_is_error(result, 1000, 'Old connection identifier.')
        self.assertNotIn(0x99887766, self.server.connids)

    async def _test_process_announce_too_frequent(self, event, mock_datetime):
        now = datetime(1854, 1, 6, 5, 23, 30, 400)
        self.server.connids[0x99887766] = now - \
                                          timedelta(seconds=self.server.connid_valid_period - 1)
        self.server.activity[0x99887766] = now - \
                                           timedelta(seconds=self.server.interval - 1)
        mock_datetime.now.return_value = now
        result = self.proto.process_announce(
            ('127.0.0.1', 8007),
            0x99887766,
            1000,
            self.create_announce_payload(
                infohash=b'01234567890123456789',
                peerid=b'98765432109876543210',
                downloaded=1000,
                left=2000,
                uploaded=500,
                event=event,
                ip='127.0.0.2',
                key=0,
                num_want=100,
                port=8009,
                ext=0))

        if event == 0:
            self.assert_is_error(result, 1000, 'Requests too frequent.')
            self.assertEqual(self.server.activity[0x99887766], now)
        else:
            self.assertGreaterEqual(len(result), 20)
            action, tid = struct.unpack('!II', result[:8])
            self.assertEqual(tid, 1000)
            self.assertEqual(action, 1)

    @patch('pybtracker.server.datetime', autospec=pybtracker.server.datetime)
    async def test_process_announce_too_frequent_regular(self, mock_datetime):
        await self._test_process_announce_too_frequent(0, mock_datetime)

    @patch('pybtracker.server.datetime', autospec=pybtracker.server.datetime)
    async def test_process_announce_too_frequent_non_regular(self, mock_datetime):
        await self._test_process_announce_too_frequent(1, mock_datetime)

    async def _test_process_announce_valid(self, num_want, mock_datetime):
        now = datetime(1854, 1, 6, 5, 23, 30, 400)
        self.server.connids[0x99887766] = now - \
                                          timedelta(seconds=self.server.connid_valid_period - 1)
        self.server.activity[0x99887766] = now - \
                                           timedelta(seconds=self.server.interval + 1)
        mock_datetime.now.return_value = now
        result = self.proto.process_announce(
            ('127.0.0.1', 8007),
            0x99887766,
            1000,
            self.create_announce_payload(
                infohash=b'01234567890123456789',
                peerid=b'98765432109876543210',
                downloaded=1000,
                left=2000,
                uploaded=500,
                event=2,
                ip='127.0.0.2',
                key=0,
                num_want=num_want,
                port=8009,
                ext=0))
        self.assertGreaterEqual(len(result), 20)

        peers = result[20:]
        peers = [(peers[i:i+4], peers[i+4:i+6])
                 for i in range(0, len(peers), 6)]
        peers = [(str(ip_address(ip)), int.from_bytes(port, byteorder='big'))
                 for ip, port in peers]
        self.assertLessEqual(len(peers), num_want)
        for i in peers:
            self.assertIn(i, [('1.1.1.1', 1111),
                              ('2.2.2.2', 2222),
                              ('3.3.3.3', 3333)])
        self.assertEqual(self.server.activity[0x99887766], now)

        ip = int.from_bytes(ip_address('127.0.0.2').packed, byteorder='big')
        self.server.announce.assert_called_with(b'01234567890123456789',
                                                b'98765432109876543210',
                                                1000, 2000, 500, 2,
                                                ip, 8009)

    @patch('pybtracker.server.datetime', autospec=pybtracker.server.datetime)
    async def test_process_announce_valid(self, mock_datetime):
        await self._test_process_announce_valid(100, mock_datetime)

    @patch('pybtracker.server.datetime', autospec=pybtracker.server.datetime)
    async def test_process_announce_valid_subset(self, mock_datetime):
        await self._test_process_announce_valid(2, mock_datetime)

    async def test_datagram_received_connect(self):
        self.proto.process_connect = Mock()
        mock_response = Mock()
        self.proto.process_connect.return_value = mock_response

        msg = struct.pack('!QII', 0x41727101980, 0, 1000)
        self.proto.datagram_received(msg, ('127.0.0.3', 8007))
        self.proto.process_connect.assert_called_with(
            ('127.0.0.3', 8007),
            0x41727101980,
            1000,
            b'')
        self.proto.transport.sendto.assert_called_with(mock_response, ('127.0.0.3', 8007))

    async def test_datagram_received_announce(self):
        self.proto.process_announce = Mock()
        mock_response = Mock()
        self.proto.process_announce.return_value = mock_response

        msg = struct.pack('!QII', 0x123456, 1, 1000) + b'X' * 160
        self.proto.datagram_received(msg, ('127.0.0.3', 8007))
        self.proto.process_announce.assert_called_with(
            ('127.0.0.3', 8007),
            0x123456,
            1000,
            b'X' * 160)
        self.proto.transport.sendto.assert_called_with(mock_response, ('127.0.0.3', 8007))

class TrackerServerTest(asynctest.TestCase):
    async def setUp(self):
        self.server = pybtracker.TrackerServer(
            local_addr=('127.0.0.1', 0))
        await self.server.start()

    async def tearDown(self):
        await self.server.stop()

    async def test_init_default(self):
        server = pybtracker.TrackerServer(
            local_addr=('127.0.0.1', 0))
        self.assertIs(server.loop, asyncio.get_event_loop())

    async def test_init_non_default(self):
        mock_loop = Mock(spec=asyncio.BaseEventLoop)
        server = pybtracker.TrackerServer(
            local_addr=('127.0.0.10', 0),
            loop=mock_loop,
            interval=1000,
            connid_valid_period=123)
        self.assertEqual(server.local_addr, ('127.0.0.10', 0))
        self.assertEqual(server.interval, 1000)
        self.assertEqual(server.connid_valid_period, 123)
        self.assertIs(server.loop, mock_loop)

    async def test_start(self):
        self.assertTrue(self.server.started_up.is_set())
        self.assertNotEqual(self.server.local_addr[1], 0)

    async def _test_announce(self, event, new_torrent, new_peer):
        # can't have an old peer for a new torrent
        assert not new_torrent or new_peer

        ih = b'01234567890123456789'
        peerid = b'98765432109876543210'
        dl = 1000
        left = 2000
        ul = 500
        ip = '11.22.33.44'
        port = 1122

        if not new_torrent:
            if new_peer:
                self.server.torrents = {
                    ih: {
                        b'11111111111111111111': ('1.1.1.1', 1111, 900, 99, 9, False)
                    }
                }
            else:
                self.server.torrents = {
                    ih: {
                        peerid: ('1.1.1.1', 1111, 900, 99, 9, False)
                    }
                }

        self.server.announce(ih, peerid, dl, left, ul, event, ip, port)

        if event not in [0, 1, 2, 3]:
            if new_torrent:
                self.assertNotIn(ih, self.server.torrents)
            elif new_peer:
                self.assertNotIn(peerid, self.server.torrents[ih])
            else:
                self.assertIn(peerid, self.server.torrents[ih])
                _ip, _port, _dl, _left, _ul, _completed = self.server.torrents[ih][peerid]
                self.assertEqual(_ip, '1.1.1.1')
                self.assertEqual(_port, 1111)
                self.assertEqual(_dl, 900)
                self.assertEqual(_left, 99)
                self.assertEqual(_ul, 9)
                self.assertFalse(_completed)
            return

        if event == 3: # stopped
            if new_torrent:
                self.assertEqual(self.server.torrents, {})
            elif new_peer:
                self.assertEqual(list(self.server.torrents[ih].keys()),
                                 [b'11111111111111111111'])
            else:
                self.assertEqual(self.server.torrents, {})
            return

        self.assertIn(ih, self.server.torrents)
        self.assertIn(peerid, self.server.torrents[ih])
        _ip, _port, _dl, _left, _ul, _completed = self.server.torrents[ih][peerid]
        self.assertEqual(_ip, ip)
        self.assertEqual(_port, port)
        self.assertEqual(_dl, dl)
        self.assertEqual(_left, left)

        if event == 1: # completed
            self.assertTrue(_completed)

    async def test_announce_new_torrent_new_peer_event_none(self):
        await self._test_announce(event=0, new_torrent=True, new_peer=True)

    async def test_announce_old_torrent_new_peer_event_none(self):
        await self._test_announce(event=0, new_torrent=False, new_peer=True)

    async def test_announce_old_torrent_old_peer_event_none(self):
        await self._test_announce(event=0, new_torrent=False, new_peer=False)

    async def test_announce_new_torrent_new_peer_event_completed(self):
        await self._test_announce(event=1, new_torrent=True, new_peer=True)

    async def test_announce_old_torrent_new_peer_event_completed(self):
        await self._test_announce(event=1, new_torrent=False, new_peer=True)

    async def test_announce_old_torrent_old_peer_event_completed(self):
        await self._test_announce(event=1, new_torrent=False, new_peer=False)

    async def test_announce_new_torrent_new_peer_event_started(self):
        await self._test_announce(event=2, new_torrent=True, new_peer=True)

    async def test_announce_old_torrent_new_peer_event_started(self):
        await self._test_announce(event=2, new_torrent=False, new_peer=True)

    async def test_announce_old_torrent_old_peer_event_started(self):
        await self._test_announce(event=2, new_torrent=False, new_peer=False)

    async def test_announce_new_torrent_new_peer_event_stopped(self):
        await self._test_announce(event=3, new_torrent=True, new_peer=True)

    async def test_announce_old_torrent_new_peer_event_stopped(self):
        await self._test_announce(event=3, new_torrent=False, new_peer=True)

    async def test_announce_old_torrent_old_peer_event_stopped(self):
        await self._test_announce(event=3, new_torrent=False, new_peer=False)

    async def test_announce_new_torrent_new_peer_event_invalid(self):
        await self._test_announce(event=4, new_torrent=True, new_peer=True)

    async def test_announce_old_torrent_new_peer_event_invalid(self):
        await self._test_announce(event=4, new_torrent=False, new_peer=True)

    async def test_announce_old_torrent_old_peer_event_invalid(self):
        await self._test_announce(event=4, new_torrent=False, new_peer=False)

class TrackerClientTest(asynctest.TestCase):
    async def setUp(self):
        pass

    async def test_init_invalid_uri(self):
        with self.assertRaises(ValueError):
            client = pybtracker.TrackerClient(
                'http://sometracker.com:8000/announce')

    async def test_init_valid(self):
        client = pybtracker.TrackerClient('udp://foo.net:8009/announce')
        self.assertEqual(client.server_addr, ('foo.net', 8009))
        self.assertEqual(client.max_retransmissions, 8)
        self.assertIs(client.loop, asyncio.get_event_loop())
        self.assertIsInstance(client.peerid, bytes)
        self.assertEqual(len(client.peerid), 20)

    async def test_init_valid_without_port(self):
        client = pybtracker.TrackerClient('udp://foo.net/announce')
        self.assertEqual(client.server_addr, ('foo.net', 80))

    async def test_init_valid_with_loop(self):
        mock_loop = Mock()
        client = pybtracker.TrackerClient('udp://foo.net/announce', loop=mock_loop)
        self.assertIs(client.loop, mock_loop)

    async def test_init_valid_with_max_transmissions(self):
        client = pybtracker.TrackerClient('udp://foo.net/announce',
                                       max_retransmissions=10)
        self.assertIs(client.max_retransmissions, 10)

    async def test_announce(self):
        client = pybtracker.TrackerClient('udp://foo.net:8001/announce')
        client.proto = CoroutineMock(autospec=pybtracker.client.UdpTrackerClientProto)
        await client.announce(b'012345678901234567890', 2000, 1000, 900, 5, 130)
        client.proto.announce.assert_called_with(
            b'012345678901234567890', 130, 2000, 1000, 900, 5)

class TrackerClientProtoTest(asynctest.TestCase):
    async def setUp(self):
        self.client = Mock(autospec=pybtracker.TrackerServer)
        self.client.connid_valid_period = 60
        self.client.connid_timestamp = datetime.now()
        self.proto = pybtracker.client.UdpTrackerClientProto(self.client)

    async def test_datagram_received_invalid(self):
        self.proto.datagram_received(b'x' * 7, ('127.0.0.1', 10000))
        self.assertIs(self.proto.received_msg, None)

    async def test_datagram_received_valid(self):
        event = asyncio.Event()
        self.proto.sent_msgs[1000] = event
        self.proto.datagram_received(
            struct.pack('!II', 5, 1000) + b'abcdefgh',
            ('127.0.0.1', 10000))
        self.assertEqual(self.proto.received_msg, (5, 1000, b'abcdefgh'))
        self.assertTrue(event.is_set())

    @patch('pybtracker.client.random')
    async def test_get_tid(self, mock_random):
        mock_random.randint.return_value = 1600001
        ret = self.proto.get_tid()
        self.assertEqual(ret, 1600001)
        self.assertIn(1600001, self.proto.sent_msgs)
        self.assertIsInstance(self.proto.sent_msgs[1600001], asyncio.Event)

    @patch('pybtracker.client.random')
    async def test_get_tid_duplicate(self, mock_random):
        mock_random.randint.side_effect = [1500001, 1600001]
        event15 = self.proto.sent_msgs[1500001] = asyncio.Event()
        ret = self.proto.get_tid()
        self.assertEqual(ret, 1600001)

        self.assertIn(1500001, self.proto.sent_msgs)
        self.assertIs(self.proto.sent_msgs[1500001], event15)

        self.assertIn(1600001, self.proto.sent_msgs)
        self.assertIsInstance(self.proto.sent_msgs[1600001], asyncio.Event)

    async def _test_send_msg(self, expected_failures, mock_asyncio):
        tid = 1000
        msg = b'x' * 20
        self.proto.sent_msgs[tid] = Mock(spec=asyncio.Event)
        self.proto.transport = Mock(spec=asyncio.DatagramTransport)
        self.proto.connect = Mock()
        wait_time = 0
        failures = 0
        async def wait_for_side_effect(task, timeout):
            nonlocal wait_time, failures
            if failures == expected_failures:
                return
            failures += 1
            wait_time += timeout
            raise asyncio.TimeoutError

        expected_wait_time = 0
        for i in range(min(expected_failures, self.client.max_retransmissions)):
            expected_wait_time += 15 * 2 ** i

        mock_asyncio.TimeoutError = asyncio.TimeoutError
        mock_asyncio.wait_for.side_effect = wait_for_side_effect
        if expected_failures > self.client.max_retransmissions:
            with self.assertRaises(TimeoutError):
                await self.proto.send_msg(msg, tid)
        else:
            await self.proto.send_msg(msg, tid)
        self.proto.transport.sendto(msg)
        self.assertEqual(wait_time, expected_wait_time)
        self.proto.connect.called_with()
        self.assertNotIn(tid, self.proto.sent_msgs)
        self.assertEqual(failures, min(expected_failures,
                                       self.client.max_retransmissions))

    @patch('pybtracker.client.asyncio')
    async def test_send_msg_immediate_success(self, mock_asyncio):
        self.client.max_retransmissions = 5
        await self._test_send_msg(0, mock_asyncio)

    @patch('pybtracker.client.asyncio')
    async def test_send_msg_success_after_failures(self, mock_asyncio):
        self.client.max_retransmissions = 5
        await self._test_send_msg(3, mock_asyncio)

    @patch('pybtracker.client.asyncio')
    async def test_send_msg_exceed_max_retransmissions(self, mock_asyncio):
        self.client.max_retransmissions = 5
        await self._test_send_msg(6, mock_asyncio)

    @patch('pybtracker.client.datetime', autospec=pybtracker.client.datetime)
    async def test_connect_success(self, mock_datetime):
        async def send_msg_side_effect(msg, tid):
            self.assertEqual(msg, struct.pack('!QII', 0x41727101980, 0, 1000))
            data = (0x500001).to_bytes(length=8, byteorder='big')
            self.proto.received_msg = 0, 1000, data
        self.proto.send_msg = Mock()
        self.proto.send_msg.side_effect = send_msg_side_effect
        self.proto.get_tid = Mock()
        self.proto.get_tid.return_value = 1000
        now = datetime(1854, 1, 6, 5, 23, 30, 400)
        mock_datetime.now.return_value = now
        await self.proto.connect()
        self.assertEqual(self.client.connid, 0x500001)
        self.assertEqual(self.client.connid_timestamp, now)

    async def test_connect_failure(self):
        async def send_msg_side_effect(msg, tid):
            self.assertEqual(msg, struct.pack('!QII', 0x41727101980, 0, 1000))
            data = b'Some error msg.'
            self.proto.received_msg = 3, 1000, data
        self.proto.send_msg = Mock()
        self.proto.send_msg.side_effect = send_msg_side_effect
        self.proto.get_tid = Mock()
        self.proto.get_tid.return_value = 1000
        with self.assertRaises(pybtracker.ServerError):
            await self.proto.connect()
        self.assertIs(self.client.connid, None)

    async def _test_announce(self, expect_connect, successful_connect=True):
        ih = b'01234567890123456789'
        peerid = b'98765432109876543210'
        ip = 0x01020304
        peers = [('10.20.30.40', 1020),
                 ('100.101.102.103', 10010),
                 ('24.28.32.36', 2428)]
        async def send_msg_side_effect(msg, tid):
            _cid, _action, _tid, _ih, _pid, _dl, _left, _ul, _ev, _ip, _k, _nw, _port = struct.unpack('!QII20s20sQQQIIIIH', msg)
            self.assertEqual(_cid, 0x500001)
            self.assertEqual(_action, 1)
            self.assertEqual(_tid, 1000)
            self.assertEqual(_ih, ih)
            self.assertEqual(_pid, peerid)
            self.assertEqual(_dl, 2000)
            self.assertEqual(_left, 1000)
            self.assertEqual(_ul, 500)
            self.assertEqual(_ev, 5)
            self.assertEqual(_ip, ip)
            self.assertEqual(_nw, 130)
            self.assertEqual(_port, 1234)
            data = struct.pack('!III', 200, 50, 60)
            data += b''.join(
                ip_address(i).packed + j.to_bytes(length=2, byteorder='big')
                for i, j in peers)
            self.proto.received_msg = 0, 1000, data

        async def connect_side_effect():
            if successful_connect:
                self.client.connid = 0x500001
            else:
                self.client.connid = None

        self.proto.connect = Mock()
        self.proto.connect.side_effect = connect_side_effect
        self.proto.send_msg = Mock()
        self.proto.send_msg.side_effect = send_msg_side_effect
        self.proto.get_tid = Mock()
        self.proto.get_tid.return_value = 1000
        self.proto.transport = Mock()
        self.proto.transport._sock.getsockname.return_value = ('1.2.3.4', 1234)
        self.client.peerid = peerid

        ret = await self.proto.announce(ih, 130, 2000, 1000, 500, 5, ip)

        if expect_connect:
            self.proto.connect.assert_called_with()
        else:
            self.proto.connect.assert_not_called()

        if successful_connect:
            self.assertEqual(self.client.interval, 200)
            self.assertIsNot(ret, None)
            self.assertEqual(len(ret), 3)
            for p in peers:
                self.assertIn(p, ret)
        else:
            self.assertIs(ret, None)

    async def test_announce_already_connected(self):
        self.client.interval = 120
        self.client.connid = 0x500001
        self.client.connid_timestamp = datetime.now() - timedelta(seconds=1)
        await self._test_announce(expect_connect=False)

    async def test_announce_connid_expired(self):
        self.client.interval = 120
        self.client.connid = 0x400001
        self.client.connid_timestamp = datetime.now() - timedelta(seconds=121)
        await self._test_announce(expect_connect=True,
                                  successful_connect=True)

    async def test_announce_no_connid(self):
        self.client.interval = 120
        self.client.connid = None
        await self._test_announce(expect_connect=True,
                                  successful_connect=True)

    async def test_announce_cant_connect(self):
        self.client.interval = 120
        self.client.connid = None
        await self._test_announce(expect_connect=True,
                                  successful_connect=False)

if __name__ == '__main__':
    import logging, sys
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
    asynctest.main()
