# Copyright (C) 2011-2012 Linaro Limited
#
# Author: Paul Larson <paul.larson@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

import atexit
import datetime
import errno
import logging
import os
import signal
import shutil
import tempfile
import threading
import time
import urlparse
import subprocess
import re

from shlex import shlex
from distutils import spawn

import pexpect

from lava_dispatcher.errors import CriticalError


def kill_process_with_option(process=None, key_option=None):
    if not process:
        return
    lines = os.popen('ps -ef')
    for line in lines:
        fields = line.split()
        if len(fields) < 8:
            continue
        # if (process in fields):
        if fields[7] and (process == fields[7]):
            if (not key_option) or (key_option in fields):
                logging_system('sudo kill -9 %s' % fields[1])


def link_or_copy_file(src, dest):
    try:
        dirname = os.path.dirname(dest)
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        os.link(src, dest)
    except OSError, err:
        if err.errno == errno.EXDEV:
            shutil.copy(src, dest)
        if err.errno == errno.EEXIST:
            logging.debug("Cached copy of %s already exists", dest)
        else:
            logging.exception("os.link '%s' with '%s' failed", src, dest)


def copy_file(src, dest):
    dirname = os.path.dirname(dest)
    if not os.path.exists(dir):
        os.makedirs(dirname)
    shutil.copy(src, dest)


def touch(path):
    with open(path, 'a'):
        os.utime(path, None)


def rmtree(directory):
    subprocess.call(['rm', '-rf', directory])


def mkdtemp(basedir='/tmp'):
    """ returns a temporary directory that's deleted when the process exits
    """

    d = tempfile.mkdtemp(dir=basedir)
    atexit.register(rmtree, d)
    os.chmod(d, 0o755)
    return d


def mk_targz(tfname, rootdir, basedir='.', asroot=False):
    """ Similar shutil.make_archive but it doesn't blow up with unicode errors
    """
    cmd = 'tar --selinux -C %s -czf %s %s' % (rootdir, tfname, basedir)
    if asroot:
        cmd = 'nice sudo %s' % cmd
    if logging_system(cmd):
        raise CriticalError('Unable to make tarball of: %s' % rootdir)


def extract_tar(tfname, tmpdir):
    """ Extracts the contents of a .tgz file to the tmpdir. It then returns
    a list of all the files (full path). This is being used to get around
    issues that python's tarfile seems to have with unicode
    """
    file_list = []
    if tfname.endswith('.bz2'):
        try:
            output = subprocess.check_output(['nice', 'tar', '--selinux', '-C',
                                              tmpdir, '-jxvf', tfname])
        except subprocess.CalledProcessError:
            raise CriticalError('Unable to extract tarball: %s' % tfname)
    elif tfname.endswith('.gz') or tfname.endswith('.tgz'):
        try:
            output = subprocess.check_output(['nice', 'tar', '--selinux', '-C',
                                              tmpdir, '-xvzf', tfname])
        except subprocess.CalledProcessError:
            raise CriticalError('Unable to extract tarball: %s' % tfname)
    elif tfname.endswith('.zip'):
        if spawn.find_executable('unzip'):
            try:
                output = subprocess.check_output(['nice', 'unzip', tfname,
                                                  '-d', tmpdir])
            except subprocess.CalledProcessError:
                raise CriticalError('Unable to extract zipfile: %s' % tfname)
        else:
            logging.error("Infrastructure Error: 'unzip' command not found")
    else:
        raise CriticalError('Unable to extract tarball: %s' % tfname)
    if output:
        files = output.split('\n')
        for f in files:
            fname = unicode(f[2:], "utf8")
            file_list.append(tmpdir + '/' + fname)

    return file_list


def extract_rootfs(rootfs, root):
    """ Extracts the contents of a .tar.(bz2, gz, xz, lzma, etc) rootfs to the root.
    """
    logging.warning('Attempting to extract tarball with --strip-components=1')
    if logging_system('nice tar --selinux --strip-components=1 -C %s -xaf %s' % (root, rootfs)):
        logging.warning('Unable to extract tarball with --strip-components=1')
        logging.warning('Cleaning up temporary directory')
        if logging_system('rm -rf %s/*' % root):
            raise CriticalError('Unable to clean up temporary directory')
        logging.warning('Attempting to extract tarball without --strip-components=1')
        if logging_system('nice tar --selinux -C %s -xaf %s' % (root, rootfs)):
            raise CriticalError('Unable to extract tarball: %s' % rootfs)
    if logging_system('rm %s' % rootfs):
        raise CriticalError('Unable to remove tarball: %s' % rootfs)


def extract_overlay(overlay, root):
    """ Extracts the contents of a compressed tar (bz2, gz, xz, lzma, etc) to the filesystem root.
    """
    logging.info('Attempting to install overlay onto the filesystem')
    if logging_system('nice tar --selinux -C %s -xaf %s' % (root, overlay)):
        raise CriticalError('Unable to extract tarball: %s to %s' % (overlay, root))
    if logging_system('rm %s' % overlay):
        raise CriticalError('Unable to remove tarball: %s' % overlay)


def extract_ramdisk(ramdisk, tmpdir, is_uboot=False):
    """ Extracts the contents of a cpio.gz filesystem to a tmp directory.
    """
    logging.info('Attempting to extract ramdisk')
    ramdisk_compressed_data = os.path.join(tmpdir, 'ramdisk.cpio.gz')
    extracted_ramdisk = os.path.join(tmpdir, 'tmp')
    if logging_system('mkdir -p %s' % extracted_ramdisk):
        raise CriticalError('Unable to create directory: %s' % extracted_ramdisk)
    if is_uboot:
        if logging_system('nice dd if=%s of=%s ibs=64 skip=1' % (ramdisk, ramdisk_compressed_data)):
            raise CriticalError('Unable to remove uboot header: %s' % ramdisk)
    if logging_system('mv %s %s' % (ramdisk, ramdisk_compressed_data)):
        raise CriticalError('Unable to rename %s to %s' % (ramdisk, ramdisk_compressed_data))
    if logging_system('nice gzip -d -f %s' % ramdisk_compressed_data):
        raise CriticalError('Unable to uncompress: %s' % ramdisk_compressed_data)
    ramdisk_data = os.path.join(tmpdir, 'ramdisk.cpio')
    if logging_system('cd %s && cpio -i -F %s' % (extracted_ramdisk, ramdisk_data)):
        raise CriticalError('Unable to uncompress: %s' % ramdisk_data)
    return extracted_ramdisk


def create_ramdisk(ramdisk_dir, tmpdir):
    """ Creates a cpio.gz filesystem from a directory
    """
    logging.info('Attempting to create ramdisk')
    ramdisk_data = os.path.join(tmpdir, 'ramdisk.cpio')
    if logging_system("cd %s && find . | cpio --create --format='newc' > %s" % (ramdisk_dir, ramdisk_data)):
        raise CriticalError('Unable to create cpio filesystem')
    if logging_system("cd %s && gzip %s" % (tmpdir, ramdisk_data)):
        raise CriticalError('Unable to compress cpio filesystem')
    if logging_system("rm -rf %s" % ramdisk_dir):
        raise CriticalError('Unable extracted ramdisk directory')
    return os.path.join(tmpdir, 'ramdisk.cpio.gz')


def is_uimage(kernel, context):
    uimage = ['u-boot', 'uImage']

    # Detect if zImage or uImage is used.
    cmd = 'file ' + kernel
    output = context.run_command_get_output(cmd)
    if any(x in output for x in uimage):
        return True
    else:
        return False


def create_uimage(kernel, load_addr, tmp_dir, xip, arch):
    load_addr = int(load_addr, 16)
    uimage_path = '%s/uImage' % tmp_dir
    if xip:
        entry_addr = load_addr + 64
    else:
        entry_addr = load_addr
    cmd = 'mkimage -A %s -O linux -T kernel \
           -C none -a 0x%x -e 0x%x \
            -d %s %s' % (arch, load_addr,
                         entry_addr, kernel,
                         uimage_path)

    logging.info('Creating uImage')
    logging.debug(cmd)
    r = subprocess.call(cmd, shell=True)

    if r == 0:
        return uimage_path
    else:
        raise CriticalError("uImage creation failed")


def create_multi_image(kernel, ramdisk, load_addr, tmp_dir, arch='arm'):
    load_addr = int(load_addr, 16)
    uimage_path = '%s/uImage' % tmp_dir
    entry_addr = load_addr
    cmd = 'mkimage -A %s -O linux -T multi \
           -C none -a 0x%x -e 0x%x \
            -d %s:%s %s' % (arch, load_addr,
                            entry_addr, kernel,
                            ramdisk, uimage_path)

    logging.info('Creating Multi Image')
    logging.debug(cmd)
    r = subprocess.call(cmd, shell=True)

    if r == 0:
        return uimage_path
    else:
        raise CriticalError("Multi Image creation failed")


def create_boot_image(mkbootimg, kernel, ramdisk, dtb, load_addr,
                      cmdline, tmp_dir, page_size=2048):
    load_addr = int(load_addr, 16)
    image_path = os.path.join(tmp_dir, 'boot-lava.img')
    cmd = '%s --kernel %s \
           --ramdisk %s \
           --output %s \
           --dt %s \
           --pagesize %s \
           --base 0x%x \
           --cmdline "%s"' % (mkbootimg,
                              kernel, ramdisk,
                              image_path, dtb,
                              page_size, load_addr,
                              cmdline)

    logging.info('Creating boot image')
    logging.debug(cmd)
    r = subprocess.call(cmd, shell=True)

    if r == 0:
        return image_path
    else:
        raise CriticalError("Boot image creation failed")


def create_dt_image(dtbtool, dtb, tmp_dir, size=2048):
    image_path = os.path.join(tmp_dir, 'dt.img')
    cmd = '%s -o %s \
           -s %s %s' % (dtbtool, image_path, size, os.path.dirname(dtb))

    logging.info('Creating DT image')
    logging.debug(cmd)
    r = subprocess.call(cmd, shell=True)

    if r == 0:
        return image_path
    else:
        raise CriticalError("DT image creation failed")


def create_fat_boot_image(kernel, tmpdir, fastboot, dtb=None, ramdisk=None):
    logging.info("Attempting to create fat boot image")
    boot_fat_dir = os.path.join(tmpdir, 'boot-fat')
    boot_fat_img = os.path.join(tmpdir, 'boot-fat.img')
    if logging_system("mkdir -p %s" % boot_fat_dir):
        raise CriticalError("Unable to create boot-fat directory")
    if logging_system("dd if=/dev/zero of=%s bs=512 count=131072" % boot_fat_img):
        raise CriticalError("Unable to create boot-fat image")
    if logging_system("mkfs.fat -n \"BOOT IMG\" %s" % boot_fat_img):
        raise CriticalError("Unable to format boot-fat image")
    if logging_system("mount -o loop,rw,sync %s %s" % (boot_fat_img, boot_fat_dir)):
        raise CriticalError("Unable to mount boot-fat image")
    if logging_system("cp -a %s %s" % (kernel, boot_fat_dir)):
        raise CriticalError('Unable copy kernel')
    if logging_system("cp -a %s %s/fastboot.efi" % (fastboot, boot_fat_dir)):
        raise CriticalError('Unable copy fastboot image')
    if ramdisk:
        if logging_system("cp -a %s %s" % (ramdisk, boot_fat_dir)):
            raise CriticalError('Unable copy ramdisk')
    if dtb:
        if logging_system("cp -a %s %s" % (dtb, boot_fat_dir)):
            raise CriticalError('Unable copy dtb')
    if logging_system("umount %s" % boot_fat_dir):
        raise CriticalError("Unable to unmount boot-fat directory")
    if logging_system("rm -rf %s" % boot_fat_dir):
        raise CriticalError('Unable remove boot-fat directory')
    return boot_fat_img


def append_dtb(kernel, dtb, tmp_dir):
    kernel_path = '%s/kernel-dtb' % tmp_dir
    cmd = 'cat %s %s > %s' % (kernel, dtb, kernel_path)

    logging.info('Appending dtb to kernel image')
    logging.debug(cmd)
    r = subprocess.call(cmd, shell=True)

    if r == 0:
        return kernel_path
    else:
        raise CriticalError("Appending dtb to kernel image failed")


def prepend_blob(kernel, blob, tmp_dir):
    kernel_path = '%s/kernel-blob' % tmp_dir
    cmd = 'cat %s %s > %s' % (blob, kernel, kernel_path)

    logging.info('Appending blob to kernel image')
    logging.debug(cmd)
    r = subprocess.call(cmd, shell=True)

    if r == 0:
        return kernel_path
    else:
        raise CriticalError("Appending blob to kernel image failed")


def ensure_directory(path):
    """ ensures the path exists, if it doesn't it will be created
    """
    if not os.path.exists(path):
        os.makedirs(path)


def ensure_directory_empty(path):
    """ Ensures the given directorty path exists, and is empty. It will delete
    The directory contents if needed.
    """
    if os.path.exists(path):
        rmtree(path)
    os.makedirs(path)


def url_to_cache(url, cachedir):
    url_parts = urlparse.urlsplit(url)
    path = os.path.join(cachedir, url_parts.netloc,
                        url_parts.path.lstrip(os.sep))
    return path


def _newlines_to_spaces(string):
    string = string.replace('\r\n', ' ')
    return string.replace('\n', ' ')


def _strip_newlines(string):
    return _newlines_to_spaces(string).strip(' ')


def string_to_list(string):
    splitter = shlex(string, posix=True)
    splitter.whitespace = ","
    splitter.whitespace_split = True
    return map(_strip_newlines, list(splitter))


def logging_system(cmd):
    ret = 1
    for i in range(1, 4):
        logging.debug("Attempt %s: Executing on host : '%s'" % (i, cmd))
        rc = os.system(cmd)
        if rc == 0:
            ret = rc
            break
        else:
            ret = rc
            time.sleep(20)
    if ret != 0:
        logging.error("Infrastructure Error: %s failed" % cmd)
    return ret


class DrainConsoleOutput(threading.Thread):

    def __init__(self, proc=None, timeout=None):
        threading.Thread.__init__(self)
        self.proc = proc
        self.timeout = timeout
        self._stopevent = threading.Event()
        self.daemon = True  # allow thread to die when main main proc exits

    def run(self):
        expect_end = None
        if self.timeout and (self.timeout > -1):
            expect_end = time.time() + self.timeout
        while not self._stopevent.isSet():
            if expect_end and (expect_end <= time.time()):
                logging.info("DrainConsoleOutput times out:%s", self.timeout)
                break
            try:
                self.proc.empty_buffer()
                time.sleep(5)
            except ValueError:
                logging.debug("pexpect ended for thread %s", self.getName())
                expect_end = time.time()

    def join(self, timeout=None):
        self._stopevent.set()
        threading.Thread.join(self, timeout)


class logging_spawn(pexpect.spawn):

    def __init__(self, command, cwd=None, timeout=30):
        pexpect.spawn.__init__(
            self, command, cwd=cwd, timeout=timeout)

        # serial can be slow, races do funny things, so increase delay
        self.delaybeforesend = 0.05

    def sendline(self, s='', delay=0, send_char=True):
        """
        Replaced sendline so that it can support the delay argument which allows a delay
        between sending each character to get around slow serial problems (iPXE).
        pexpect sendline does exactly the same thing: calls send for the string then os.linesep.
        :param s: string to send
        :param delay: delay in milliseconds between sending each character
        :param send_char: send one character or entire string
        """
        self.send(s, delay, send_char)
        self.send(os.linesep, delay)

    def sendcontrol(self, char):
        logging.debug("sending control character: %s", char)
        return super(logging_spawn, self).sendcontrol(char)

    def send(self, string, delay=0, send_char=True):
        logging.debug("send (delay_ms=%s): %s ", delay, string)
        sent = 0
        delay = float(delay) / 1000
        if send_char:
            for char in string:
                sent += super(logging_spawn, self).send(char)
                time.sleep(delay)
        else:
            sent = super(logging_spawn, self).send(string)
        return sent

    def expect(self, *args, **kw):
        # some expect should not be logged because it is so much noise.
        lava_logging = True
        if 'lava_no_logging' in kw:
            del kw['lava_no_logging']
            lava_logging = False

        if 'timeout' in kw:
            timeout = kw['timeout']
        else:
            timeout = self.timeout

        if len(args) == 1:
            if lava_logging:
                logging.debug("expect (%d): '%s'", timeout, args[0])
        else:
            if lava_logging:
                logging.debug("expect (%d): '%s'", timeout, str(args))

        try:
            proc = super(logging_spawn, self).expect(*args, **kw)
        except pexpect.EOF:
            raise CriticalError(" ".join(self.before.split('\r\n')))
        return proc

    def empty_buffer(self):
        """Make sure there is nothing in the pexpect buffer."""
        index = 0
        while index == 0:
            index = self.expect(
                ['.+', pexpect.EOF, pexpect.TIMEOUT],
                timeout=0.1, lava_no_logging=1)


def connect_to_serial(context):
    """
    Attempts to connect to a serial console server like conmux or cyclades
    """
    retry_count = 0
    retry_limit = 99999

    port_stuck_message = 'Data Buffering Suspended\.'
    conn_closed_message = 'Connection closed by foreign host\.'
    connection_refused_message = 'Connection refused'

    expectations = {
        port_stuck_message: 'reset-port',
        context.device_config.connection_command_pattern: 'all-good',
        conn_closed_message: 'retry',
        pexpect.TIMEOUT: 'retry',
        connection_refused_message: 'retry',
    }
    patterns = []
    results = []
    for pattern, result in expectations.items():
        patterns.append(pattern)
        results.append(result)

    while retry_count < retry_limit:
        try:
            proc = context.spawn(
                context.device_config.connection_command,
                timeout=120)
            logging.info('Attempting to connect to device using: %s', context.device_config.connection_command)
            match = proc.expect(patterns, timeout=10)
            result = results[match]
            logging.info('Matched %r which means %s', patterns[match], result)
            if result == 'retry' or result == 'reset-port':
                reset_cmd = context.device_config.reset_port_command
                if reset_cmd:
                    logging.warning('attempting to reset serial port')
                    context.run_command(reset_cmd)
                else:
                    logging.warning('no reset_port command configured')
                proc.close(True)
                retry_count += 1
                time.sleep(5)
                continue
            elif result == 'all-good':
                context.test_data.add_result('connect_to_console', 'pass')
                atexit.register(proc.close, True)
                return proc
        except CriticalError:
            retry_count += 1

    msg = 'Infrastructure Error: cannot connect to console.'
    logging.error(msg)
    context.test_data.add_result('connect_to_console', 'fail',
                                 message=msg)
    raise CriticalError('could not execute connection_command successfully')


def wait_for_prompt(connection, prompt_pattern, timeout, skip_newlines=False):
    # One of the challenges we face is that kernel log messages can appear
    # half way through a shell prompt.  So, if things are taking a while,
    # we send a newline along to maybe provoke a new prompt.  We wait for
    # half the timeout period and then wait for one tenth of the timeout
    # 6 times (so we wait for 1.1 times the timeout period overall).
    prompt_wait_count = 0
    if timeout == -1:
        timeout = connection.timeout
    partial_timeout = timeout / 2.0
    while True:
        try:
            connection.expect(prompt_pattern, timeout=partial_timeout)
        except pexpect.TIMEOUT:
            if prompt_wait_count < 6:
                prompt_wait_count += 1
                partial_timeout = timeout / 10
                if not skip_newlines:
                    logging.warning('Sending newline in case of corruption.')
                    connection.sendline('')
                continue
            else:
                raise
        else:
            break


# XXX Duplication: we should reuse lava-test TestArtifacts
def generate_bundle_file_name(test_name):
    return ("{test_id}.{time.tm_year:04}-{time.tm_mon:02}-{time.tm_mday:02}T"
            "{time.tm_hour:02}:{time.tm_min:02}:{time.tm_sec:02}Z")\
        .format(test_id=test_name, time=datetime.datetime.utcnow().timetuple())


def finalize_process(proc):
    if proc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            logging.debug("Finalizing child process group with PID %d", proc.pid)
        except OSError:
            proc.kill(9)
            logging.debug("Finalizing child process with PID %d", proc.pid)
        proc.close()


def read_content(filepath, ignore_missing=False):
    if not os.path.exists(filepath) and ignore_missing:
        return ''
    with open(filepath, 'r') as f:
        return f.read()


def write_content(filename, content):
    with open(filename, 'a') as f:
        f.write(content)


def indices(string, char):
    """
    Return the indices of the given character in the given string.
    Return an empty list if the character cannot be found.
    """
    return [i for i, c in enumerate(string) if c == char]


def search_substr_from_array(string, array, sep=','):
    """
    Return True if any of the string in 'array' is a substring in 'string'
    Strings in array are separated by default with ','
    """
    for obj in array.split(sep):
        if re.search('.*' + str(obj) + '.*', str(string)):
            return True
    return False


def unicode_path_check(path):
    """
    Safe check for paths which may include unicode characters
    :param path: path to check with exists.
    :return: False if None, else return value of os.path.exists
    """
    if not path:
        return False
    return os.path.exists(path)
