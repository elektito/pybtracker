import asyncio
import struct
import logging
from ipaddress import ip_address
from random import randint, sample
from datetime import datetime, timedelta
from version import __version__

DEFAULT_INTERVAL = 300


class UdpTrackerServerProto(asyncio.Protocol):
    def __init__(self, server, allowed_torrents=None):
        self.server = server
        self.logger = server.logger
        self.connection_lost_received = asyncio.Event()
        self.allowed_torrents = allowed_torrents
        self.transport = None

    def error(self, tid, msg):
        return struct.pack('!II', 3, tid) + msg

    def process_connect(self, addr, connid, tid, data):
        self.server.logger.info('Received connect message.')
        if connid == 0x41727101980:
            connid = randint(0, 0xffffffffffffffff)
            self.server.connids[connid] = datetime.now()
            self.server.activity[addr] = datetime.now()
            return struct.pack('!IIQ', 0, tid, connid)
        else:
            return self.error(tid, 'Invalid protocol identifier.'.encode('utf-8'))

    def process_announce(self, addr, connid, tid, data):
        self.server.logger.info('Received announce message.')

        # remove extensions
        if len(data) > 82:
            data = data[:82]

        # parse the request
        ih, peerid, dl, left, ul, ev, ip, k, num_want, port = struct.unpack('!20s20sQQQIIIIH', data)

        # use the ip address in the message if it's provided
        if ip == 0:
            ip = addr[0]

        # make sure the provided connection identifier is valid
        timestamp = self.server.connids.get(connid, None)
        last_valid = datetime.now() - timedelta(seconds=self.server.connid_valid_period)
        if not timestamp:
            # we didn't generate that connection identifier
            return self.error(tid, 'Invalid connection identifier.'.encode('utf-8'))
        elif timestamp < last_valid:
            # we did generate that identifier, but it's too
            # old. remove it and send an error.
            del self.server.connids[connid]
            return self.error(tid, 'Old connection identifier.'.encode('utf-8'))
        elif self.allowed_torrents and ih not in self.allowed_torrents:
            return self.error(tid, 'Unknown/Forbidden torrent.'.encode('utf-8'))
        else:
            if ev == 0:
                # make sure this client is not sending regular
                # announces too frequently
                allowed = datetime.now() - timedelta(seconds=self.server.interval)
                if connid in self.server.activity \
                        and self.server.activity[connid] > allowed:
                    self.server.activity[connid] = datetime.now()
                    return self.error(
                        tid, 'Requests too frequent.'.encode('utf-8'))

            self.server.activity[connid] = datetime.now()

            # send the event to the tracker
            self.server.announce(ih, peerid, dl, left, ul, ev, ip, port)

            # get all peers for this torrent
            all_peers = self.server.torrents.get(ih, {}).values()

            # count all peers that have announced "completed". these
            # are the seeders. the rest are leechers.
            seeders = sum(1 for _, _, _, _, _, completed in all_peers
                          if completed)
            leechers = len(all_peers) - seeders

            # we're not interested in anything but (ip, port) pairs
            # anymore
            all_peers = [(ip, port) for ip, port, dl, left, ul, c in all_peers]

            # remove this peer from the list
            all_peers = [i for i in all_peers if i != addr]

            # we can't give more peers than we've got
            num_want = min(num_want, len(all_peers))

            # get a random sample from the peers
            peers = sample(all_peers, num_want)

            # now pack the (ip, port) pairs
            peers = b''.join(
                (ip_address(p[0]).packed + p[1].to_bytes(length=2, byteorder='big'))
                for p in peers)

            # construct and return the response
            return struct.pack(
                '!IIIII',
                1, tid, self.server.interval, leechers, seeders) + peers

    def connection_made(self, transport):
        self.transport = transport

    def connection_lost(self, exc):
        self.connection_lost_received.set()

    def datagram_received(self, data, addr):
        if len(data) < 16:
            self.logger.warning('Datagram smaller than 16 bytes.')
            return

        connid, action, tid = struct.unpack('!QII', data[:16])
        resp = {
            0: self.process_connect,
            1: self.process_announce
        }.get(action, lambda a, c, t, d: None)(addr, connid,
                                               tid, data[16:])

        if resp:
            self.transport.sendto(resp, addr)

    def error_received(self, exc):
        self.logger.info('Error received:'.format(exc))


def read_whitelist_file(logger, filename):
    infohashes = set()
    with open(filename, 'r') as f:
        for line in f:
            if not line:
                continue
            try:
                ih = bytes.fromhex(line.strip())
            except ValueError:
                logger.error('Invalid infohash in whitelist file.')
            else:
                infohashes.add(ih)

    return infohashes


class TrackerServer:
    def __init__(self,
                 local_addr=('127.0.0.1', 6881),
                 interval=DEFAULT_INTERVAL,
                 connid_valid_period=120,
                 allowed_torrents=None,
                 loop=None):
        self.local_addr = local_addr
        self.interval = interval
        self.connid_valid_period = connid_valid_period
        self.allowed_torrents = allowed_torrents

        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()

        self.activity = {}
        self.connids = {}
        self.torrents = {}
        self.transport = None
        self.proto = None
        self.started_up = asyncio.Event()
        self.logger = logging.getLogger(__name__)

    async def watcher_whitelist(self, file_path):
        while True:
            await asyncio.sleep(10)
            try:
                allowed_torrents = read_whitelist_file(self.logger, file_path)
                if allowed_torrents ^ self.allowed_torrents:
                    self.logger.info('Whitelist updated.')
                    self.allowed_torrents = allowed_torrents
                    self.proto.allowed_torrents = allowed_torrents
            except IOError:
                self.logger.error('Whitelist cannot be opened')

    async def start(self):
        self.transport, self.proto = await self.loop.create_datagram_endpoint(
            lambda: UdpTrackerServerProto(self, self.allowed_torrents),
            local_addr=self.local_addr)
        self.local_addr = self.transport._sock.getsockname()
        self.logger.info('Started listening on {}:{}.'.format(*self.local_addr))
        self.started_up.set()

    async def stop(self):
        self.transport.close()
        await self.proto.connection_lost_received.wait()
        self.logger.info('Tracker stopped.')

    def announce(self, ih, peerid, dl, left, ul, ev, ip, port):
        if ev not in [0, 1, 2, 3]:
            self.logger.warning('Invalid event in announce.')
            return

        if ih not in self.torrents:
            self.logger.info('New info hash encountered: {}'.format(ih.hex()))
            self.torrents[ih] = {}
            self.torrents[ih][peerid] = (ip, port, 0, 0, 0, (ev == 1))
        if ih in self.torrents and peerid not in self.torrents[ih]:
            self.logger.debug('New peer encountered: {}'.format(peerid.hex()))
            self.torrents[ih][peerid] = (ip, port, 0, 0, 0, (ev == 1))

        if ev == 0:
            # none
            self.logger.info('Regular announce from: {}'.format(peerid.hex()))

            _ip, _port, _dl, _left, _ul, _completed = self.torrents[ih][peerid]
            if _ip != ip or _port != port:
                self.logger.info('Peer "{}" announcing from new address {}:{}'
                                 .format(peerid.hex(), ip, port))
            self.torrents[ih][peerid] = (ip, port, dl, left, ul, _completed)
        elif ev == 1:
            # completed
            self.logger.info('Completion announce from: {}'.format(peerid.hex()))
            self.torrents[ih][peerid] = (ip, port, dl, left, ul, True)
        elif ev == 2:
            # started
            self.logger.info('Start announce from: {}'.format(peerid.hex()))
            self.torrents[ih][peerid] = (ip, port, dl, left, ul, True)
        elif ev == 3:
            # stopped
            self.logger.info('Stop announce from: {}. Removed peer.'.format(peerid.hex()))
            del self.torrents[ih][peerid]
            if self.torrents[ih] == {}:
                del self.torrents[ih]


def end_point(v):
    if ':' in v:
        host, port = v.split(':')
    else:
        host, port = v, 8000

    if host == '':
        host = '127.0.0.1'

    if port == '':
        port = '8000'

    port = int(port)

    return host, port


def setup_logging(args):
    import sys
    logger = logging.getLogger(__name__)
    formatter = logging.Formatter(
        '%(asctime) -15s - %(levelname) -8s - %(message)s')
    level = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warning': logging.WARNING,
        'error': logging.ERROR,
        'critical': logging.CRITICAL
    }[args.log_level]

    if args.log_to_stdout:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        logger.addHandler(handler)

    if args.log_file:
        handler = logging.FileHandler(args.log_file)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        logger.addHandler(handler)

    logger.setLevel(level)

    return logger


def main():
    import argparse

    parser = argparse.ArgumentParser(description='UDP tracker.')
    parser.add_argument(
        '--bind', '-b', default='127.0.0.1:8000', type=end_point,
        metavar='HOST:PORT',
        help='The address to bind to. Defaults to 127.0.0.1:8000')
    parser.add_argument(
        '--whitelist', '-w', default='',
        help='Whitelist with torrent infohashes.')
    parser.add_argument(
        '--log-to-stdout', '-O', action='store_true', default=False,
        help='Log to standard output.')
    parser.add_argument('--log-file', '-l', help='Log to the specified file.')
    parser.add_argument(
        '--log-level', '-L', default='info',
        choices=['debug', 'info', 'warning', 'error', 'critical'],
        help='Set log level. Defaults to "info".')
    parser.add_argument(
        '--version', '-V', action='version',
        version='pybtracker v' + __version__)

    args = parser.parse_args()

    logger = setup_logging(args)

    loop = asyncio.get_event_loop()

    allowed_torrents = {}

    if args.whitelist:
        allowed_torrents = read_whitelist_file(logger, args.whitelist)

    tracker = TrackerServer(local_addr=args.bind,
                            loop=loop,
                            allowed_torrents=allowed_torrents)

    if args.whitelist:
        asyncio.ensure_future(tracker.watcher_whitelist(args.whitelist))
    asyncio.ensure_future(tracker.start())

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print()
        loop.run_until_complete(tracker.stop())


if __name__ == '__main__':
    main()
