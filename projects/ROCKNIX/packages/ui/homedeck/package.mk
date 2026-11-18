# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026-present ROCKNIX (https://github.com/ROCKNIX)

PKG_NAME="homedeck"
PKG_VERSION="1.0"
PKG_LICENSE="GPL-2.0-or-later"
PKG_SITE="https://github.com/ROCKNIX/distribution"
PKG_DEPENDS_TARGET="toolchain Python3 SDL2 SDL2_image SDL2_mixer SDL2_ttf"
PKG_LONGDESC="3DS-style home menu for dual-screen handhelds, with EmulationStation as the classic view."
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/share/homedeck
    cp ${PKG_DIR}/sources/*.py ${INSTALL}/usr/share/homedeck
  mkdir -p ${INSTALL}/usr/bin
    cp ${PKG_DIR}/scripts/homedeck ${INSTALL}/usr/bin
  chmod 0755 ${INSTALL}/usr/bin/homedeck
}

post_install() {
  enable_service homedeck.service
}
