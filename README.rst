pybtracker
==========

pybtracker is a UDP BitTorrent tracker written in Python 3.5 using
co-routines and the ``asyncio`` module. You can use the tracker in a
script like this:

:: code:: python

   import asyncio
   from pybtracker import TrackerServer

   loop = asyncio.get_event_loop()
   tracker = TrackerServer(local_addr=('127.0.0.1', 8000), loop=loop)
   asyncio.ensure_future(tracker.start())
   try:
       loop.run_forever()
   except KeyboardInterrupt:
       loop.run_until_complete(tracker.stop())

It also includes a UDP tracker client:

:: code:: python

   import asyncio
   from pybtracker import TrackerClient

   async def announce():
       client = TrackerClient(announce_uri='udp://127.0.0.1:8000', loop=loop)
       await client.start()
       peers = await client.announce(
           b'01234567890123456789',  # infohash
           10000,                    # downloaded
           40000,                    # left
           5000,                     # uploaded
           0,                        # event (0=none)
           120                       # number of peers wanted
       )
       print(peers)

   loop = asyncio.get_event_loop()
   loop.run_until_complete(announce())

You can run the server independently by running:

:: code:: sh

   $ python -m pybtracker.server -b 127.0.0.1:8000 -O

The client can also be run independently and it provides you with a
shell to interact with the server:

:: code:: sh

   $ python -m pybtracker.client udp://127.0.0.1:8000
   BitTorrent tracker client. Type help or ? to list commands.

   (btrc) help
   Documented commands (type help <topic>):
   ========================================
   EOF  announce  connect  help  quit

   (btrc) quit
   $

If you have installed pybtracker using ``pip`` or ``setup.py``, you
can also run ``pybtracker`` and ``pybtracker-client`` instead of using
``python -m``.
