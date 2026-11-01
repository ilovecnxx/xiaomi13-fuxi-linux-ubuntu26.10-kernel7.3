#!/system/bin/sh
# 在 Android 里（adb root）从卡 2（中国电信）拨一通电话，选卡后校验；落到别的卡就立刻挂断。
# 用法：fuxi-android-dial-sim2.sh <号码> <验证后保持秒数>
# 输出不含号码。只读 UI；不改任何设置。
NUM="$1"; HOLD="${2:-12}"
OUT=/data/local/tmp/dial-sim2.log
: > $OUT
say() { echo "$(date +%T) $*" | tee -a $OUT; }
dump() { uiautomator dump /sdcard/ui.xml >/dev/null 2>&1; tr '>' '\n' < /sdcard/ui.xml; }
ui_has() { dump | grep -q "$1"; }
callstate() { dumpsys telephony.registry 2>/dev/null | grep -m1 -o "mCallState=[0-9]"; }

input keyevent KEYCODE_WAKEUP
sleep 1
BEFORE=$(logcat -d -s TelecomFramework:I 2>/dev/null | grep -a "createConnection, callManagerAccount" | tail -1)
am start -a android.intent.action.CALL -d "tel:$NUM" >/dev/null 2>&1
say "call intent sent"

TAPPED=0
for i in 1 2 3 4 5 6 7 8; do
  D=$(dump)
  if echo "$D" | grep -q "选择 SIM 卡"; then
    B=$(echo "$D" | grep 'text="中国电信"' | grep -o 'bounds="\[[0-9]*,[0-9]*\]\[[0-9]*,[0-9]*\]"' | head -1 | tr -dc '0-9,[]' )
    X1=$(echo "$B" | sed -E 's/\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]/\1/')
    Y1=$(echo "$B" | sed -E 's/\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]/\2/')
    X2=$(echo "$B" | sed -E 's/\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]/\3/')
    Y2=$(echo "$B" | sed -E 's/\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]/\4/')
    if [ -n "$X1" ] && [ -n "$Y2" ]; then
      input tap $(( (X1 + X2) / 2 )) $(( (Y1 + Y2) / 2 ))
      say "picker seen, tapped 中国电信 at $(( (X1 + X2) / 2 )),$(( (Y1 + Y2) / 2 ))"
      TAPPED=1
      break
    fi
  fi
  if echo "$D" | grep -q "通过China Unicom\|通过中国联通"; then
    say "call already on Unicom, hanging up"
    input keyevent KEYCODE_ENDCALL
    exit 2
  fi
done
[ "$TAPPED" = 1 ] || { say "picker not found, hanging up"; input keyevent KEYCODE_ENDCALL; exit 3; }

# 校验：读 telecom 实际选中的账户（TelephonyConnectionService 的账户 id 2 = 订阅 2 = 中国电信，界面文字不可靠）
OK=0
for i in 1 2 3 4 5 6; do
  AFTER=$(logcat -d -s TelecomFramework:I 2>/dev/null | grep -a "createConnection, callManagerAccount" | tail -1)
  if [ -n "$AFTER" ] && [ "$AFTER" != "$BEFORE" ]; then
    case "$AFTER" in
      *"TelephonyConnectionService}, 2, "*) OK=1 ;;
      *) say "WRONG ACCOUNT, hanging up"; input keyevent KEYCODE_ENDCALL; exit 2 ;;
    esac
    break
  fi
  sleep 1
done
if [ "$OK" = 1 ]; then
  say "verified: telecom connection account 2 (SIM 2, Telecom)"
else
  say "cannot verify account from telecom log, hanging up to be safe"
  input keyevent KEYCODE_ENDCALL
  exit 4
fi

# 拨出阶段 Android 就已是 OFFHOOK/IN_CALL，没有可靠的接通信号；按固定时间：验证电信卡后保持 HOLD 秒再挂断，用户在此期间接听
say "holding ${HOLD}s for the callee to answer"
sleep "$HOLD"
input keyevent KEYCODE_ENDCALL
sleep 2
say "ended; state: $(callstate)"
rm -f /sdcard/ui.xml
