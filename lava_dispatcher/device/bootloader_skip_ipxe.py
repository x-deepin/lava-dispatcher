import os
import shutil
from lava_dispatcher.device.bootloader import (
    BootloaderTarget
)
from lava_dispatcher.utils import (
    connect_to_serial,
)


class BootloaderSkipIPXETarget(BootloaderTarget):

    def __init__(self, context, config):
        super(BootloaderTarget, self).__init__(context, config)
        self._booted = False
        self._reset_boot = False
        self._in_test_shell = False
        self._default_boot_cmds = 'boot_cmds_ramdisk'
        self._lava_nfsrootfs = None
        self._uboot_boot = False
        self._ipxe_boot = False
        self._uefi_boot = False
        self._boot_tags = {}
        self._base_tmpdir, self._tmpdir = self._setup_tmpdir()

    def _run_boot(self):
        self._load_test_firmware()
        boot_cmds = self._load_boot_cmds(default=self._default_boot_cmds,
                                         boot_tags=self._boot_tags)
        with open("/var/lib/tftpboot/%s/stage2.ipxe" % self.config.ip_address, "w") as f:
            f.write("#!ipxe\n")
            for cmd in boot_cmds:
                f.write(cmd + "\n")

        if hasattr(self, "_lava_nfsrootfs") and self._lava_nfsrootfs:
            shutil.copy("/usr/local/bin/busybox_lava", self._lava_nfsrootfs + "/bin/")
            if not os.path.exists(self._lava_nfsrootfs + "/etc/systemd/system/lava-telnetd.service"):
                with open(self._lava_nfsrootfs + "/etc/systemd/system/lava-telnetd.service", "w") as f:
                    f.write("\n".join(["[Unit]",
                                       "Description=Telnetd for LAVA",
                                       "After=network.target",
                                       "",
                                       "[Service]",
                                       "ExecStart=/bin/busybox_lava telnetd -p 12345 -F -l/bin/bash"
                                       ]))
                os.symlink("/etc/systemd/system/lava-telnetd.service",
                           self._lava_nfsrootfs + "/etc/systemd/system/multi-user.target.wants/lava-telnetd.service")

    def _boot_linaro_image(self):
        self._run_boot()
        self.proc = connect_to_serial(self.context)

        if self._is_bootloader() and not self._booted:
            if self.config.hard_reset_command or self.config.hard_reset_command == "":
                self._hard_reboot(self.proc)
                self._run_boot()
            else:
                self._soft_reboot(self.proc)
                self._run_boot()
            self._booted = True
        elif self._is_bootloader() and self._booted:
            self.proc.sendline('export PS1="%s"'
                               % self.tester_ps1,
                               send_char=self.config.send_char)
        else:
            super(BootloaderTarget, self)._boot_linaro_image()

target_class = BootloaderSkipIPXETarget
