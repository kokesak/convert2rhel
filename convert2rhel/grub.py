# -*- coding: utf-8 -*-
#
# Copyright(C) 2021 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

__metaclass__ = type


import os
import re

from convert2rhel import systeminfo, utils
from convert2rhel.logger import root_logger


logger = root_logger.getChild(__name__)

GRUB2_BIOS_ENTRYPOINT = "/boot/grub2"
"""The entrypoint path of the BIOS GRUB2"""

GRUB2_BIOS_CONFIG_FILE = os.path.join(GRUB2_BIOS_ENTRYPOINT, "grub.cfg")
"""The path to the configuration file for GRUB2 in BIOS"""

GRUB2_BIOS_ENV_FILE = os.path.join(GRUB2_BIOS_ENTRYPOINT, "grubenv")
"""The path to the env file for GRUB2 in BIOS"""

EFI_MOUNTPOINT = "/boot/efi/"
"""The path to the required mountpoint for ESP."""

CENTOS_EFIDIR_CANONICAL_PATH = os.path.join(EFI_MOUNTPOINT, "EFI/centos/")
"""The canonical path to the default UEFI directory on for CentOS Linux system."""

RHEL_EFIDIR_CANONICAL_PATH = os.path.join(EFI_MOUNTPOINT, "EFI/redhat/")
"""The canonical path to the default UEFI directory on for RHEL system."""

# TODO(pstodulk): following constants are valid only for x86_64 arch
DEFAULT_INSTALLED_EFIBIN_FILENAMES = ("shimx64.efi", "grubx64.efi")
"""Filenames of the UEFI binary files that could be installed by default on the system.

Sorted by the recommended preferences. The first one (shimx64.efi) is preferred, but it
is provided by the shim rpm which does not have to be installed on the system.
In case it's missing, another files should be used instead.
"""


class BootloaderError(Exception):
    """The generic error related to this module."""

    def __init__(self, message):
        super(BootloaderError, self).__init__(message)
        self.message = message


class UnsupportedEFIConfiguration(BootloaderError):
    """Raised when the bootloader UEFI configuration seems unsupported.

    E.g. when we expect the ESP is mounted to /boot/efi but it is not.
    """


class EFINotUsed(BootloaderError):
    """Raised when UEFI is expected but BIOS is detected instead."""


def is_efi():
    """Return True if UEFI is used.

    NOTE(pstodulk): the check doesn't have to be valid for hybrid boot (e.g. AWS, Azure, ..)
    """
    return os.path.exists("/sys/firmware/efi")


def is_secure_boot():
    """Return True if the secure boot is enabled."""
    # Secure boot is only applicable to UEFI
    if not is_efi():
        return False
    try:
        stdout, ecode = utils.run_subprocess(["mokutil", "--sb-state"], print_output=False)
    except OSError:
        logger.debug("The mokutil utility for secure boot is not installed. Secure boot is likely not enabled.")
        return False
    # output will be "SecureBoot {enabled|disabled}"
    if ecode or "enabled" not in stdout:
        return False
    return True


def _get_partition(directory):
    """Return the disk partition for the specified directory.

    Raise BootloaderError if the partition can't be detected.
    """
    stdout, ecode = utils.run_subprocess(["/usr/sbin/grub2-probe", "--target=device", directory], print_output=False)
    if ecode or not stdout:
        logger.error("grub2-probe returned %s. Output:\n%s" % (ecode, stdout))
        raise BootloaderError("Unable to get device information for %s." % directory)
    return stdout.strip()


def get_boot_partition():
    """Return the disk partition with /boot present.

    Raise BootloaderError if the partition can't be detected.
    """
    return _get_partition("/boot")


def get_efi_partition():
    """Return the EFI System Partition (ESP).

    Raise EFINotUsed if UEFI is not detected.
    Raise UnsupportedEFIConfiguration when ESP is not mounted where expected.
    Raise BootloaderError if the partition can't be obtained from GRUB.
    """
    if not is_efi():
        raise EFINotUsed("Unable to get ESP when BIOS is used.")
    if not os.path.exists(EFI_MOUNTPOINT) or not os.path.ismount(EFI_MOUNTPOINT):
        raise UnsupportedEFIConfiguration(
            "The UEFI has been detected but the ESP is not mounted in /boot/efi as required."
        )
    return _get_partition(EFI_MOUNTPOINT)


def _get_blk_device(device):
    """Get the block device.

    In case of the block device itself (e.g. /dev/sda), return just the block
    device. In case of a partition, return its block device:
        /dev/sda  -> /dev/sda
        /dev/sda1 -> /dev/sda

    Raise the BootloaderError when unable to get the block device.
    """
    output, ecode = utils.run_subprocess(["lsblk", "-spnlo", "name", device], print_output=False)
    if ecode:
        logger.debug("lsblk output:\n-----\n%s\n-----" % output)
        raise BootloaderError("Unable to get a block device for '%s'." % device)

    return output.strip().splitlines()[-1].strip()


def get_device_number(device):
    """Get the partition number of a particular device.

    This method will use `blkid` to determinate what is the partition number
    related to a particular device.

    :param device: The device to be analyzed.
    :type device: str
    :return: The device partition number.
    :rtype: int
    """
    output, ecode = utils.run_subprocess(
        ["/usr/sbin/blkid", "-p", "-s", "PART_ENTRY_NUMBER", device], print_output=False
    )
    output = output.strip()
    if ecode:
        logger.debug("blkid output:\n-----\n%s\n-----" % output)
        raise BootloaderError("Unable to get information about the '%s' device" % device)
    # We are spliting the partition entry number, and we are just taking that
    # output as our desired partition number
    if not output:
        raise BootloaderError("The '%s' device has no PART_ENTRY_NUMBER" % device)
    partition_number = output.split("PART_ENTRY_NUMBER=")[-1].replace('"', "")
    return int(partition_number)


def get_grub_device():
    """Get the block device on which GRUB is installed.

    We assume GRUB is on the same device as /boot (or ESP).
    """
    partition = get_efi_partition() if is_efi() else get_boot_partition()
    return _get_blk_device(partition)


class EFIBootLoader:
    """Representation of an UEFI boot loader entry."""

    def __init__(self, boot_number, label, active, efi_bin_source):
        self.boot_number = boot_number
        """Expected string, e.g. '0001'. """

        self.label = label
        """Label of the UEFI entry. E.g. 'Centos'"""

        self.active = active
        """True when the UEFI entry is active (asterisk is present next to the boot number)"""

        self.efi_bin_source = efi_bin_source
        """Source of the UEFI binary.

        It could contain various values, e.g.:
            FvVol(7cb8bdc9-f8eb-4f34-aaea-3ee4af6516a1)/FvFile(462caa21-7614-4503-836e-8ab6f4662331)
            HD(1,GPT,28c77f6b-3cd0-4b22-985f-c99903835d79,0x800,0x12c000)/File(\\EFI\\redhat\\shimx64.efi)
            PciRoot(0x0)/Pci(0x2,0x3)/Pci(0x0,0x0)N.....YM....R,Y.
        """

    def __eq__(self, other):
        return all(
            [
                self.boot_number == other.boot_number,
                self.label == other.label,
                self.active == other.active,
                self.efi_bin_source == other.efi_bin_source,
            ]
        )

    def __ne__(self, other):
        return not self.__eq__(other)

    def is_referring_to_file(self):
        """Return True when the boot source is a file.

        Some sources could refer e.g. to PXE boot. Return true if the source
        refers to a file ("ends with /File(...path...)")

        Does not matter whether the file exists or not.
        """
        return "/File(\\" in self.efi_bin_source

    @staticmethod
    def _efi_path_to_canonical(efi_path):
        return os.path.join(EFI_MOUNTPOINT, efi_path.replace("\\", "/").lstrip("/"))

    def get_canonical_path(self):
        """Return expected canonical path for the referred UEFI bin or None.

        Return None in case the entry is not referring to any UEFI bin
        (e.g. when it refers to a PXE boot).
        """
        if not self.is_referring_to_file():
            return None
        match = re.search(r"/File\((?P<path>\\.*)\)$", self.efi_bin_source)
        return EFIBootLoader._efi_path_to_canonical(match.groups("path")[0])


class EFIBootInfo:
    """Data about the current UEFI boot configuration.

    Raise BootloaderError when unable to obtain info about the UEFI configuration.
    Raise EFINotUsed when BIOS is detected.
    Raise UnsupportedEFIConfiguration when ESP is not mounted where expected.
    """

    def __init__(self):
        if not is_efi():
            raise EFINotUsed("Unable to collect data about UEFI on a BIOS system.")
        bootmgr_output, ecode = utils.run_subprocess(["/usr/sbin/efibootmgr", "-v"], print_output=False)
        if ecode:
            raise BootloaderError("Unable to get information about UEFI boot entries.")

        self.current_bootnum = None
        """The boot number (str) of the current boot."""
        self.boot_order = tuple()
        """The tuple of the UEFI boot loader entries in the boot order."""
        self.entries = {}
        """The UEFI boot loader entries {'boot_number': EFIBootLoader}"""

        self._parse_efi_boot_entries(bootmgr_output)
        self._parse_current_bootnum(bootmgr_output)
        self._parse_boot_order(bootmgr_output)
        self._print_loaded_info()

    def _parse_efi_boot_entries(self, bootmgr_output):
        """Return dict of UEFI boot loader entries: {"<boot_number>": EFIBootLoader}"""
        self.entries = {}
        regexp_entry = re.compile(
            r"^Boot(?P<bootnum>[a-zA-Z0-9]+)(?P<active>\*?)\s*(?P<label>.*?)\t(?P<bin_source>.*)$"
        )
        for line in bootmgr_output.splitlines():
            match = regexp_entry.match(line)
            if not match:
                continue

            self.entries[match.group("bootnum")] = EFIBootLoader(
                boot_number=match.group("bootnum"),
                label=match.group("label"),
                active="*" in match.group("active"),
                efi_bin_source=match.group("bin_source"),
            )
        if not self.entries:
            # it's not expected that no entry exists
            raise BootloaderError("UEFI: Unable to detect any UEFI bootloader entry.")

    def _parse_current_bootnum(self, bootmgr_output):
        # e.g.: BootCurrent: 0002
        for line in bootmgr_output.splitlines():
            if line.startswith("BootCurrent:"):
                self.current_bootnum = line.split(":")[1].strip()
                return
        raise BootloaderError("UEFI: Unable to detect current boot number.")

    def _parse_boot_order(self, bootmgr_output):
        # e.g.:  BootOrder: 0001,0002,0000,0003
        for line in bootmgr_output.splitlines():
            if line.startswith("BootOrder:"):
                self.boot_order = tuple(line.split(":")[1].strip().split(","))
                return
        raise BootloaderError("UEFI: Unable to detect current boot order.")

    def _print_loaded_info(self):
        msg = "Bootloader setup:"
        msg += "\nCurrent boot: %s" % self.current_bootnum
        msg += "\nBoot order: %s\nBoot entries:" % ", ".join(self.boot_order)
        for bootnum, entry in self.entries.items():
            msg += "\n- %s: %s" % (bootnum, entry.label.rstrip())
        logger.debug(msg)


def canonical_path_to_efi_format(canonical_path):
    r"""Transform the canonical path to the UEFI format.

    e.g. /boot/efi/EFI/redhat/shimx64.efi -> \EFI\redhat\shimx64.efi
    (just single backslash; so the string needs to be put into apostrophes
    when used for /usr/sbin/efibootmgr cmd)

    The path has to start with /boot/efi otherwise the path is invalid for UEFI.
    """
    # We want to keep the last "/" of the EFI_MOUNTPOINT
    return canonical_path.replace(EFI_MOUNTPOINT[:-1], "").replace("/", "\\")


def _is_rhel_in_boot_entries(efibootinfo, efi_path, label):
    """
    Verify that Red Hat Enterprise Linux is present within the boot
    loader and the bin file matches.
    """
    for i in efibootinfo.entries.values():
        if i.label == label and efi_path in i.efi_bin_source:
            return True
    return False


def _add_rhel_boot_entry(efibootinfo_orig):
    """
    Create a new UEFI bootloader entry with a RHEL label and bin file.

    If an entry for the label and bin file already exists no new entry
    will be created.

    Return the new bootloader info (EFIBootInfo).
    """
    dev_number = get_device_number(get_efi_partition())
    blk_dev = get_grub_device()

    logger.debug("Block device: %s" % str(blk_dev))
    logger.debug("ESP device number: %s" % str(dev_number))

    efi_path = None
    for filename in DEFAULT_INSTALLED_EFIBIN_FILENAMES:
        tmp_efi_path = os.path.join(RHEL_EFIDIR_CANONICAL_PATH, filename)
        if os.path.exists(tmp_efi_path):
            efi_path = canonical_path_to_efi_format(tmp_efi_path)
            logger.debug("The new UEFI binary: %s" % tmp_efi_path)
            break
    if not efi_path:
        raise BootloaderError("Unable to detect any RHEL UEFI binary file.")

    label = "Red Hat Enterprise Linux %s" % str(systeminfo.system_info.version.major)
    logger.info("Adding '%s' UEFI bootloader entry." % label)

    if _is_rhel_in_boot_entries(efibootinfo_orig, efi_path, label):
        logger.info("The '%s' UEFI bootloader entry is already present." % label)
        return efibootinfo_orig

    # The new boot entry is being set as first in the boot order
    cmd = [
        "/usr/sbin/efibootmgr",
        "--create",
        "--disk",
        blk_dev,
        "--part",
        str(dev_number),
        "--loader",
        efi_path,
        "--label",
        label,
    ]

    stdout, ecode = utils.run_subprocess(cmd, print_output=False)
    if ecode:
        logger.debug("efibootmgr output:\n-----\n%s\n-----" % stdout)
        raise BootloaderError("Unable to add a new UEFI bootloader entry for RHEL.")

    # check that our new entry exists
    logger.info("Verifying the new UEFI bootloader entry.")
    efibootinfo_new = EFIBootInfo()
    if not _is_rhel_in_boot_entries(efibootinfo_new, efi_path, label):
        raise BootloaderError("Unable to find the new UEFI bootloader entry.")

    logger.info("The '%s' bootloader entry has been added." % label)
    return efibootinfo_new


def _remove_orig_boot_entry(efibootinfo_orig, efibootinfo_new):
    """Remove the original boot entry if ...

        - the referred UEFI binary file doesn't exist anymore and originally
          was located in the default directory for the original OS
        - the referred UEFI binary file is same as the current default one

    The conditions could be more complicated if algorithms in this module
    are changed. Additional checks are implemented that could prevent the
    original boot from the removal.

    The function is expected to be called after the new RHEL entry is added
    and it expects to get EFIBootInfo before the new bootloader entry is added.
    """
    orig_boot_entry = efibootinfo_new.entries.get(efibootinfo_orig.current_bootnum, None)
    if not orig_boot_entry:
        logger.info(
            "The original, currenly used bootloader entry '%s' (%s) has been removed already."
            % (efibootinfo_orig.current_bootnum, efibootinfo_orig.entries[efibootinfo_orig.current_bootnum].label)
        )
        return

    if orig_boot_entry != efibootinfo_orig.entries[orig_boot_entry.boot_number]:
        logger.warning(
            "The original, currenly used bootloader entry '%s' (%s) has been modified. Skipping the removal."
            % (orig_boot_entry.boot_number, orig_boot_entry.label)
        )
        return

    efibin_path_orig = orig_boot_entry.get_canonical_path()
    if not efibin_path_orig:
        logger.warning(
            "Skipping the removal of the original, currenly used bootloader entry '%s' (%s):"
            " Unable to get path of its UEFI binary file." % (orig_boot_entry.boot_number, orig_boot_entry.label)
        )
        return

    # Get UEFI bin path of the new boot entry (which is being set as the first in the boot order when added)
    efibin_path_new = efibootinfo_new.entries[efibootinfo_new.boot_order[0]].get_canonical_path()
    if os.path.exists(efibin_path_orig) and efibin_path_orig != efibin_path_new:
        logger.warning(
            "Skipping the removal of the original, currenly used bootloader entry '%s' (%s):"
            " Its UEFI binary file still exists: %s"
            % (orig_boot_entry.boot_number, orig_boot_entry.label, efibin_path_orig)
        )
        return
    _, ecode = utils.run_subprocess(["/usr/sbin/efibootmgr", "-Bb", orig_boot_entry.boot_number], print_output=False)
    if ecode:
        # this is not a critical issue; the entry will be even removed
        # automatically if it is invalid (points to non-existing efibin)
        logger.warning("The removal of the original, currenly used UEFI bootloader entry has failed.")
        return
    logger.info(
        "The removal of the original, currenly used UEFI bootloader entry '%s' (%s) has been successful."
        % (orig_boot_entry.boot_number, orig_boot_entry.label)
    )


def replace_efi_boot_entry():
    """Replace the current UEFI bootloader entry with the RHEL one.

    The current UEFI bootloader entry could be invalid or misleading. It's
    expected that the new bootloader entry will refer to one of the standard UEFI binary
    files provided by Red Hat inside the RHEL_EFIDIR_CANONICAL_PATH.
    The new UEFI bootloader entry is always created / registered and set
    set as default.

    The current (original) UEFI bootloader entry is removed under some conditions
    (see _remove_orig_boot_entry() for more info).
    """
    # load the bootloader configuration NOW - after the grub files are copied
    logger.info("Loading the bootloader configuration.")
    efibootinfo_orig = EFIBootInfo()

    logger.info("Adding a new UEFI bootloader entry for RHEL.")
    efibootinfo_new = _add_rhel_boot_entry(efibootinfo_orig)

    logger.info("Removing the original UEFI bootloader entry.")
    _remove_orig_boot_entry(efibootinfo_orig, efibootinfo_new)


def get_grub_config_file():
    """Get the grub config file path.

    This method will return the grub config file, depending if it is BIOS or
    UEFI, the method will handle that automatically.

    :return: The path to the grub config file.
    :rtype: str
    """
    grub_config_path = GRUB2_BIOS_CONFIG_FILE

    if is_efi():
        grub_config_path = os.path.join(RHEL_EFIDIR_CANONICAL_PATH, "grub.cfg")

    return grub_config_path


def _log_critical_error(title):
    logger.critical(
        "%s\n"
        "The migration of the bootloader setup was not successful.\n"
        "Do not reboot your machine before doing a manual check of the\n"
        "bootloader configuration. Ensure that grubenv and grub.cfg files\n"
        "are present in the %s directory and that\n"
        "a new bootloader entry for Red Hat Enterprise Linux exists\n"
        "(check `efibootmgr -v` output).\n"
        "The entry should point to '\\EFI\\redhat\\shimx64.efi'." % (title, RHEL_EFIDIR_CANONICAL_PATH)
    )
