#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]]
src="$1"; root="$2"; kit="$3"; out="$4"
mkdir -p "$root" "$out"
debootstrap --arch=amd64 --variant=minbase --keyring=/usr/share/keyrings/debian-archive-keyring.gpg trixie "$root" https://deb.debian.org/debian
printf 'exit 101\n' > "$root/usr/sbin/policy-rc.d"
sed -i '1i#!/bin/sh' "$root/usr/sbin/policy-rc.d"
chmod 755 "$root/usr/sbin/policy-rc.d"
mount -t proc proc "$root/proc"
mount --bind /dev "$root/dev"
mount --bind /sys "$root/sys"
cleanup(){ umount "$root/sys" || true; umount "$root/dev" || true; umount "$root/proc" || true; }
trap cleanup EXIT
chroot "$root" apt-get update
chroot "$root" env DEBIAN_FRONTEND=noninteractive apt-get -y --no-install-recommends install linux-image-amd64 systemd-sysv dbus dbus-user-session libpam-systemd polkitd lightdm lightdm-gtk-greeter xserver-xorg-core xserver-xorg-input-libinput xserver-xorg-video-vesa xserver-xorg-video-fbdev x11-xserver-utils xauth xfonts-base chromium chromium-sandbox xdg-utils desktop-file-utils python3 python3-venv python3-tk ca-certificates curl xdotool fonts-dejavu-core
mkdir -p "$root/opt/maj-vaj"
tar -C "$src" --exclude=.git --exclude=node_modules -cf - . | tar -C "$root/opt/maj-vaj" -xf -
chown -R root:root "$root/opt/maj-vaj"
chmod -R a+rX,go-w "$root/opt/maj-vaj"
chroot "$root" python3 -m venv /opt/maj-vaj/venv
chroot "$root" /opt/maj-vaj/venv/bin/python -m pip install --disable-pip-version-check -r /opt/maj-vaj/requirements.txt
chroot "$root" /opt/maj-vaj/venv/bin/python -m pip check
install -m755 "$kit/guest-setup.sh" "$root/root/d2-setup.sh"
chroot "$root" sh -c 'echo "root:d2-serial-fixture-714" | chpasswd'
printf 'maj-vaj-d2\n' > "$root/etc/hostname"
printf '127.0.0.1 localhost maj-vaj-d2\n' > "$root/etc/hosts"
printf '/dev/vda / ext4 defaults 0 1\n' > "$root/etc/fstab"
rm -f "$root/usr/sbin/policy-rc.d"
chroot "$root" systemctl enable serial-getty@ttyS0.service
chroot "$root" systemctl set-default multi-user.target
chroot "$root" apt-get clean
chroot "$root" dpkg --audit > "$out/dpkg-audit.txt"
test ! -s "$out/dpkg-audit.txt"
chroot "$root" dpkg-query -W chromium chromium-sandbox xdg-utils lightdm xserver-xorg-core python3 > "$out/packages.txt"
cp "$(ls -t "$root"/boot/vmlinuz-* | head -1)" "$out/vmlinuz"
cp "$(ls -t "$root"/boot/initrd.img-* | head -1)" "$out/initrd.img"
cleanup
trap - EXIT
truncate -s 8G "$out/guest.ext4"
mkfs.ext4 -q -F -d "$root" "$out/guest.ext4"
chown "${SUDO_UID:-1000}:${SUDO_GID:-1000}" "$out/guest.ext4" "$out/vmlinuz" "$out/initrd.img" "$out/packages.txt" "$out/dpkg-audit.txt"
