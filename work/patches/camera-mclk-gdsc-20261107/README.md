# 相机第 1 步：MCLK stuck off 修法（2026-11-07，未上机）

基于收敛树 `fuxi/consolidate-7.4` a5dcbb9cc，`git apply` 即可（语音树 `~/.cache/fuxi-voice74` 也已打上）。

- 【推测，依据厂商 DTB】camcc MCLK 分支在 CAM_CC_TITAN_TOP_GDSC 里（厂商传感器节点 `cam_clk-supply = cam_cc_titan_top_gdsc`），
  7.3 上 GDSC 没开 → `cam_cc_mclk1_clk status stuck at 'off'`、clk_prepare_enable -16（docs/相机调研.md）。
- 改动：四个 `xiaomi,fuxi-camera-power` 节点加 `power-domains = <&camcc CAM_CC_TITAN_TOP_GDSC>`；
  fuxi-camera-power.c 加 runtime PM（上电前 pm_runtime_resume_and_get，断电/出错时 put）；
  sm8550-xiaomi-fuxi-camera.dts 改为叠在完整日常 DTS（sm8550-xiaomi-fuxi.dts）上，不再用 -min。
- 已验证：相机 DTB 编译无警告；和日常 DTB 比（去掉数值后 diff），只多开 cci0/cci1/camss、cci1 pinctrl、几路 LDO 电压。
- 未验证：内核没编、没上机。下一步见 TASKS.md（deb6）。
