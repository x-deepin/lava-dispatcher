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
        if self.proc:
            # TODO: reboot properly
            return

        self._run_boot()

        if self.config.power_on_cmd:
            self.context.run_command(self.config.power_on_cmd)

        self.proc = connect_to_serial(self.context)

        self._booted = True
        self.proc.sendline('export PS1="%s"'
                           % self.tester_ps1,
                           send_char=self.config.send_char)

target_class = BootloaderSkipIPXETarget
