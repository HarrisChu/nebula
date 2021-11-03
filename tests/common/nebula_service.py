# --coding:utf-8--
#
# Copyright (c) 2020 vesoft inc. All rights reserved.
#
# This source code is licensed under Apache 2.0 License.

import os
import subprocess
import time
import random
import shutil
import socket
import glob
import signal
import copy
from contextlib import closing

NEBULA_START_COMMAND_FORMAT = "bin/nebula-{} --flagfile conf/nebula-{}.conf {}"


class NebulaProcess(object):
    def __init__(self, name, ports, suffix_index=0, params=None):
        if params is None:
            params = {}
        assert len(ports) == 4, 'should have 4 ports but have {}'.format(len(ports))
        self.name = name
        self.tcp_port, self.tcp_internal_port, self.http_port, self.https_port = ports
        self.suffix_index = suffix_index
        self.params = params
        self.host = '127.0.0.1'
        self.pid = None
        pass

    def update_meta_server_addrs(self, address):
        self.params['meta_server_addrs'] = address

    def copy_conf(self):
        pass

    def _format_nebula_command(self):
        process_params = {
            'log_dir': 'logs{}'.format(self.suffix_index),
            'pid_file': 'pids{}/nebula-{}.pid'.format(self.suffix_index, self.name),
            'port': self.tcp_port,
            'ws_http_port': self.http_port,
            'ws_h2_port': self.https_port,
        }
        # data path
        if self.name.upper() != 'GRAPHD':
            process_params['data_path'] = 'data{}/{}'.format(
                self.suffix_index, self.name
            )

        process_params.update(self.params)
        cmd = [
            'bin/nebula-{}'.format(self.name),
            '--flagfile',
            'conf/nebula-{}.conf'.format(self.name),
        ] + ['--{}={}'.format(key, value) for key, value in process_params.items()]

        return " ".join(cmd)

    def start(self):
        cmd = self._format_nebula_command()
        print("exec: " + cmd)
        p = subprocess.Popen([cmd], shell=True, stdout=subprocess.PIPE)
        p.wait()
        if p.returncode != 0:
            print("error: " + bytes.decode(p.communicate()[0]))
        self.pid = p.pid

    def kill(self, sig):
        if not self.is_alive():
            return
        try:
            os.kill(self.pid, sig)
        except OSError as err:
            print("stop nebula-{} {} failed: {}".format(self.name, self.pid, str(err)))

    def is_alive(self):
        if self.pid is None:
            return False

        process = subprocess.Popen(
            ['ps', '-eo', 'pid,args'], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout = process.communicate()
        for line in bytes.decode(stdout[0]).splitlines():
            p = line.lstrip().split(' ', 1)[0]
            if str(p) == str(self.pid):
                return True
        return False


class NebulaService(object):
    def __init__(
        self,
        build_dir,
        src_dir,
        metad_num=1,
        storaged_num=1,
        graphd_num=1,
        ca_signed='false',
        debug_log='true',
        **kwargs,
    ):
        self.build_dir = str(build_dir)
        self.src_dir = str(src_dir)
        self.work_dir = os.path.join(
            self.build_dir,
            'server_' + time.strftime('%Y-%m-%dT%H-%M-%S', time.localtime()),
        )
        self.pids = {}
        self.metad_num, self.storaged_num, self.graphd_num = (
            metad_num,
            storaged_num,
            graphd_num,
        )
        self.metad_processes, self.storaged_processes, self.graphd_processes = (
            [],
            [],
            [],
        )
        self.all_processes = []
        self.metad_param, self.storaged_param, self.graphd_param = {}, {}, {}
        self.ca_signed = ca_signed
        self.debug_log = debug_log
        self.ports_per_process = 4
        self._make_params(**kwargs)

    def _make_params(self, **kwargs):
        _params = {
            'heartbeat_interval_secs': 1,
            'expired_time_factor': 60,
        }
        if self.ca_signed:
            _params['ca_path'] = 'share/resources/test.ca.pem'
            _params['cert_path'] = 'share/resources/test.derive.crt'
            _params['key_path'] = 'share/resources/test.derive.key'

        else:
            _params['ca_path'] = 'share/resources/test.ca.pem'
            _params['cert_path'] = 'share/resources/test.ca.key'
            _params['key_path'] = 'share/resources/test.ca.password'

        if self.debug_log:
            _params['v'] = '4'

        self.graphd_param = copy.copy(_params)
        self.graphd_param['local_config'] = 'false'
        self.graphd_param['enable_authorize'] = 'true'
        self.graphd_param['system_memory_high_watermark_ratio'] = '0.95'
        self.graphd_param['num_rows_to_check_memory'] = '4'
        self.graphd_param['session_reclaim_interval_secs'] = '2'
        self.storaged_param = copy.copy(_params)
        self.storaged_param['local_config'] = 'false'
        self.storaged_param['raft_heartbeat_interval_secs'] = '30'
        self.storaged_param['skip_wait_in_rate_limiter'] = 'true'
        self.metad_param = copy.copy(_params)
        for p in [self.metad_param, self.storaged_param, self.graphd_param]:
            p.update(kwargs)

    def set_work_dir(self, work_dir):
        self.work_dir = work_dir

    def _copy_nebula_conf(self):
        bin_path = self.build_dir + '/bin/'
        conf_path = self.src_dir + '/conf/'

        for item in ['nebula-graphd', 'nebula-storaged', 'nebula-metad']:
            shutil.copy(bin_path + item, self.work_dir + '/bin/')
            shutil.copy(
                conf_path + '{}.conf.default'.format(item),
                self.work_dir + '/conf/{}.conf'.format(item),
            )

        # gflags.json
        resources_dir = self.work_dir + '/share/resources/'
        os.makedirs(resources_dir)

        shutil.copy(self.build_dir + '/../resources/gflags.json', resources_dir)
        # cert files
        shutil.copy(self.src_dir + '/tests/cert/test.ca.key', resources_dir)
        shutil.copy(self.src_dir + '/tests/cert/test.ca.pem', resources_dir)
        shutil.copy(self.src_dir + '/tests/cert/test.ca.password', resources_dir)
        shutil.copy(self.src_dir + '/tests/cert/test.derive.key', resources_dir)
        shutil.copy(self.src_dir + '/tests/cert/test.derive.crt', resources_dir)

    @staticmethod
    def is_port_in_use(port):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            return s.connect_ex(('localhost', port)) == 0

    @staticmethod
    def get_free_port():
        for _ in range(30):
            try:
                with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
                    s.bind(('', random.randint(10000, 20000)))
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    return s.getsockname()[1]
            except OSError as e:
                pass

    # TODO(yee): Find free port range
    def _find_free_port(self, count):
        assert count % self.ports_per_process == 0
        all_ports = []
        for i in range(count):
            if i % self.ports_per_process == 0:
                for _ in range(100):
                    tcp_port = NebulaService.get_free_port()
                    # force internal tcp port with port+1
                    if all((tcp_port + i) not in all_ports for i in range(0, 2)):
                        all_ports.append(tcp_port)
                        all_ports.append(tcp_port + 1)
                        break

            elif i % self.ports_per_process == 1:
                continue
            else:
                for _ in range(100):
                    port = NebulaService.get_free_port()
                    if port not in all_ports:
                        all_ports.append(port)
                        break

        return all_ports

    def _telnet_port(self, port):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sk:
            sk.settimeout(1)
            result = sk.connect_ex(('127.0.0.1', port))
            return result == 0

    def install(self):
        if os.path.exists(self.work_dir):
            shutil.rmtree(self.work_dir)
        os.mkdir(self.work_dir)
        print("work directory: " + self.work_dir)
        os.chdir(self.work_dir)
        installed_files = ['bin', 'conf', 'scripts']
        for f in installed_files:
            os.mkdir(self.work_dir + '/' + f)
        self._copy_nebula_conf()

    def _check_servers_status(self, ports):
        ports_status = {}
        for port in ports:
            ports_status[port] = False

        for i in range(0, 20):
            for port in ports_status:
                if ports_status[port]:
                    continue
                if self._telnet_port(port):
                    ports_status[port] = True
            is_ok = True
            for port in ports_status:
                if not ports_status[port]:
                    is_ok = False
            if is_ok:
                return True
            time.sleep(1)
        return False

    def start(self):
        os.chdir(self.work_dir)
        process_count = self.metad_num + self.storaged_num + self.graphd_num
        ports_count = process_count * self.ports_per_process
        all_ports = self._find_free_port(ports_count)
        index = 0

        for suffix_index in range(self.metad_num):
            metad = NebulaProcess(
                "metad",
                all_ports[index : index + self.ports_per_process],
                suffix_index,
                self.metad_param,
            )
            self.metad_processes.append(metad)
            index += self.ports_per_process

        for suffix_index in range(self.storaged_num):
            storaged = NebulaProcess(
                "storaged",
                all_ports[index : index + self.ports_per_process],
                suffix_index,
                self.storaged_param,
            )
            self.storaged_processes.append(storaged)
            index += self.ports_per_process

        for suffix_index in range(self.graphd_num):
            graphd = NebulaProcess(
                "graphd",
                all_ports[index : index + self.ports_per_process],
                suffix_index,
                self.graphd_param,
            )
            self.graphd_processes.append(graphd)
            index += self.ports_per_process

        self.all_processes = (
            self.metad_processes + self.storaged_processes + self.graphd_processes
        )
        # update meta address
        meta_server_addrs = ','.join(
            [
                '{}:{}'.format(process.host, process.tcp_port)
                for process in self.metad_processes
            ]
        )
        for p in self.all_processes:
            p.update_meta_server_addrs(meta_server_addrs)

        max_suffix = max([self.graphd_num, self.storaged_num, self.metad_num])
        for i in range(max_suffix):
            os.mkdir(self.work_dir + '/logs{}'.format(i))
            os.mkdir(self.work_dir + '/pids{}'.format(i))

        start_time = time.time()
        for p in self.all_processes:
            p.start()

        # wait nebula start
        server_ports = [p.tcp_port for p in self.all_processes]
        if not self._check_servers_status(server_ports):
            self._collect_pids()
            self.kill_all(signal.SIGKILL)
            elapse = time.time() - start_time
            raise Exception(f'nebula servers not ready in {elapse}s')

        self._collect_pids()

        return [p.tcp_port for p in self.graphd_processes]

    def _collect_pids(self):
        for pf in glob.glob(self.work_dir + '/pid*/*.pid'):
            with open(pf) as f:
                self.pids[f.name] = int(f.readline())

    def stop(self, cleanup=True):
        print("try to stop nebula services...")
        self._collect_pids()
        self.kill_all(signal.SIGTERM)

        max_retries = 20
        while self.is_procs_alive() and max_retries >= 0:
            time.sleep(1)
            max_retries = max_retries - 1

        self.kill_all(signal.SIGKILL)

        if cleanup:
            shutil.rmtree(self.work_dir, ignore_errors=True)

    def kill_all(self, sig):
        for p in self.pids:
            self.kill(p, sig)

    def kill(self, pid, sig):
        if not self.is_proc_alive(pid):
            return
        try:
            os.kill(self.pids[pid], sig)
        except OSError as err:
            print("stop nebula {} failed: {}".format(pid, str(err)))

    def is_procs_alive(self):
        return any(self.is_proc_alive(pid) for pid in self.pids)

    def is_proc_alive(self, pid):
        process = subprocess.Popen(
            ['ps', '-eo', 'pid,args'], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout = process.communicate()
        for line in bytes.decode(stdout[0]).splitlines():
            p = line.lstrip().split(' ', 1)[0]
            if str(p) == str(self.pids[pid]):
                return True
        return False
