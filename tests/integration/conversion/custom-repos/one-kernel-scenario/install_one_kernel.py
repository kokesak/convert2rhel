import pytest


def install_one_kernel(shell):

    # installing kernel package
    assert shell("yum install kernel-3.10.0-1160.el7.x86_64 -y").returncode == 0
    # set deafault kernel
    assert shell("grub2-set-default 'CentOS Linux (3.10.0-1160.el7.x86_64) 7 (Core)'").returncode == 0

