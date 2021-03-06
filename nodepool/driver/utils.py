# Copyright (C) 2018 Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import logging
import threading
import time

from kazoo import exceptions as kze

from nodepool import stats
from nodepool import zk


class NodeLauncher(threading.Thread,
                   stats.StatsReporter,
                   metaclass=abc.ABCMeta):
    '''
    Class to launch a single node within a thread and record stats.

    At this time, the implementing class must manage this thread.
    '''

    def __init__(self, zk_conn, node, provider_config):
        '''
        :param ZooKeeper zk_conn: The ZooKeeper connection object.
        :param Node node: A Node object describing the node to launch.
        :param ProviderConfig provider_config: A ProviderConfig object
            describing the provider launching this node.
        '''
        threading.Thread.__init__(self, name="NodeLauncher-%s" % node.id)
        stats.StatsReporter.__init__(self)
        self.log = logging.getLogger("nodepool.NodeLauncher-%s" % node.id)
        self.zk = zk_conn
        self.node = node
        self.provider_config = provider_config

    @abc.abstractmethod
    def launch(self):
        pass

    def run(self):
        start_time = time.monotonic()
        statsd_key = 'ready'

        try:
            self.launch()
        except kze.SessionExpiredError:
            # Our node lock is gone, leaving the node state as BUILDING.
            # This will get cleaned up in ZooKeeper automatically, but we
            # must still set our cached node state to FAILED for the
            # NodeLaunchManager's poll() method.
            self.log.error(
                "Lost ZooKeeper session trying to launch for node %s",
                self.node.id)
            self.node.state = zk.FAILED
            statsd_key = 'error.zksession'
        except Exception as e:
            self.log.exception("Launch failed for node %s:", self.node.id)
            self.node.state = zk.FAILED
            self.zk.storeNode(self.node)

            if hasattr(e, 'statsd_key'):
                statsd_key = e.statsd_key
            else:
                statsd_key = 'error.unknown'

        try:
            dt = int((time.monotonic() - start_time) * 1000)
            self.recordLaunchStats(statsd_key, dt)
            self.updateNodeStats(self.zk, self.provider_config)
        except Exception:
            self.log.exception("Exception while reporting stats:")
