#!/usr/bin/env bash
#
# A USB touchscreen that stops responding after the machine has been idle,
# and comes back when you unplug it, is almost always USB autosuspend: the
# kernel powers the device down after inactivity and it does not wake up.
#
# This finds the device, shows what its power setting is, lets you prove the
# theory before changing anything, and writes a udev rule if you want it made
# permanent.
#
#   ./touchscreen-keepalive.sh              what is connected, and its state
#   ./touchscreen-keepalive.sh --wake       turn autosuspend off until reboot
#   ./touchscreen-keepalive.sh --install    write the udev rule
#   ./touchscreen-keepalive.sh --uninstall  remove it again
#
# Only --install and --uninstall need sudo, and both say what they are about
# to do first.

set -uo pipefail

RULE_FILE="/etc/udev/rules.d/50-touchscreen-keepalive.rules"
USB_ROOT="/sys/bus/usb/devices"

BOLD=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GREEN=$'\e[32m'
YELLOW=$'\e[33m'; RESET=$'\e[0m'
if [ ! -t 1 ]; then
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

say()  { printf '%s\n' "$*"; }
head_() { printf '\n%s%s%s\n' "$BOLD" "$*" "$RESET"; }
warn() { printf '%s%s%s\n' "$YELLOW" "$*" "$RESET"; }
bad()  { printf '%s%s%s\n' "$RED" "$*" "$RESET"; }
good() { printf '%s%s%s\n' "$GREEN" "$*" "$RESET"; }

read_attr() {
    # read_attr <dir> <name> [default]
    if [ -r "$1/$2" ]; then
        tr -d '\n' < "$1/$2"
    else
        printf '%s' "${3-}"
    fi
}

require() {
    command -v "$1" >/dev/null 2>&1 || {
        bad "This needs '$1', which is not installed."
        say "  Debian/Ubuntu/Mint:  sudo apt install $2"
        exit 1
    }
}

## ── finding the touchscreen ──────────────────────────────────────────────────
#
# Walked up from the input device rather than guessed from the USB product
# name. Plenty of touchscreens describe themselves as something unhelpful, and
# the kernel already knows which input devices report absolute coordinates.

find_touch_devices() {
    # Prints one "syspath<TAB>name" per USB device backing a touch input.
    local input name usbpath
    for input in /sys/class/input/input*; do
        [ -d "$input" ] || continue

        # A touchscreen reports ABS_X/ABS_Y and BTN_TOUCH. A mouse does not.
        local abs
        abs="$(read_attr "$input" "capabilities/abs" "0")"
        [ "$abs" = "0" ] && continue

        name="$(read_attr "$input" "name" "unknown")"

        # Walk up until a directory that looks like a USB device.
        usbpath="$(cd -P "$input" 2>/dev/null && pwd -P)" || continue
        while [ -n "$usbpath" ] && [ "$usbpath" != "/" ]; do
            if [ -r "$usbpath/idVendor" ] && [ -r "$usbpath/idProduct" ]; then
                printf '%s\t%s\n' "$usbpath" "$name"
                break
            fi
            usbpath="$(dirname "$usbpath")"
        done
    done | sort -u
}

describe_device() {
    local path="$1" label="${2-}"
    local vid pid manufacturer product control autosuspend

    vid="$(read_attr "$path" idVendor "????")"
    pid="$(read_attr "$path" idProduct "????")"
    manufacturer="$(read_attr "$path" manufacturer "")"
    product="$(read_attr "$path" product "")"
    control="$(read_attr "$path" power/control "n/a")"
    autosuspend="$(read_attr "$path" power/autosuspend_delay_ms "n/a")"

    printf '  %s%s:%s%s' "$BOLD" "$vid" "$pid" "$RESET"
    [ -n "$product" ] && printf '  %s' "$product"
    [ -n "$manufacturer" ] && printf ' %s(%s)%s' "$DIM" "$manufacturer" "$RESET"
    printf '\n'
    [ -n "$label" ] && printf '    input        %s\n' "$label"
    printf '    sysfs        %s%s%s\n' "$DIM" "$path" "$RESET"

    if [ "$control" = "on" ]; then
        printf '    autosuspend  %soff - this device will not be suspended%s\n' \
            "$GREEN" "$RESET"
    elif [ "$control" = "auto" ]; then
        printf '    autosuspend  %sON after %sms - this is the usual cause%s\n' \
            "$YELLOW" "$autosuspend" "$RESET"
    else
        printf '    autosuspend  %s\n' "$control"
    fi
}

parent_hub() {
    # The hub a device is plugged into, if it has one.
    local path="$1" parent
    parent="$(dirname "$path")"
    if [ -r "$parent/idVendor" ]; then
        printf '%s' "$parent"
    fi
}

## ── actions ──────────────────────────────────────────────────────────────────

do_status() {
    head_ "Touch devices on USB"

    local found=0 line path name
    while IFS=$'\t' read -r path name; do
        [ -n "$path" ] || continue
        found=1
        describe_device "$path" "$name"

        local hub
        hub="$(parent_hub "$path")"
        if [ -n "$hub" ]; then
            local hub_control
            hub_control="$(read_attr "$hub" power/control "n/a")"
            if [ "$hub_control" = "auto" ]; then
                printf '    %shub          the hub it is on can also suspend%s\n' \
                    "$YELLOW" "$RESET"
                printf '                 %s%s%s\n' "$DIM" "$hub" "$RESET"
            fi
        fi
        printf '\n'
    done < <(find_touch_devices)

    if [ "$found" = "0" ]; then
        warn "No USB touch devices found."
        say ""
        say "  That means either the screen is not connected over USB, or it"
        say "  has already stopped responding and dropped off the bus. If it is"
        say "  currently dead, unplug and replug it and run this again."
        say ""
        say "  Everything on USB right now:"
        if command -v lsusb >/dev/null 2>&1; then
            lsusb | sed 's/^/    /'
        else
            say "    (install usbutils for lsusb)"
        fi
        return 1
    fi

    if [ -f "$RULE_FILE" ]; then
        good "A keepalive rule is installed:"
        sed 's/^/    /' "$RULE_FILE"
    else
        say "${DIM}No keepalive rule installed yet.${RESET}"
    fi

    head_ "Kernel-wide setting"
    local global="/sys/module/usbcore/parameters/autosuspend"
    if [ -r "$global" ]; then
        local value
        value="$(cat "$global")"
        if [ "$value" = "-1" ]; then
            good "  usbcore.autosuspend = -1 (autosuspend disabled everywhere)"
        else
            say "  usbcore.autosuspend = ${value} ${DIM}(seconds; -1 disables it)${RESET}"
        fi
    fi
}

do_wake() {
    head_ "Turning autosuspend off until the next reboot"

    local found=0 path name
    while IFS=$'\t' read -r path name; do
        [ -n "$path" ] || continue
        found=1

        local targets=("$path")
        local hub
        hub="$(parent_hub "$path")"
        # The hub too: a suspended hub takes everything on it down with it,
        # and the device's own setting cannot help then.
        [ -n "$hub" ] && targets+=("$hub")

        local target
        for target in "${targets[@]}"; do
            if [ ! -w "$target/power/control" ] && [ "$(id -u)" != "0" ]; then
                say "  ${DIM}needs sudo:${RESET} $target"
                if ! echo on | sudo tee "$target/power/control" >/dev/null 2>&1; then
                    bad "  could not write $target/power/control"
                    continue
                fi
            else
                if ! echo on > "$target/power/control" 2>/dev/null; then
                    bad "  could not write $target/power/control"
                    continue
                fi
            fi
            good "  on  <- $target/power/control"
        done
    done < <(find_touch_devices)

    [ "$found" = "0" ] && { warn "Nothing to do - no touch devices found."; return 1; }

    say ""
    say "This lasts until reboot and proves the theory. Leave the panel idle"
    say "for as long as it usually takes to fail. If it survives, run:"
    say ""
    say "    ${BOLD}$0 --install${RESET}"
    say ""
    say "If it still dies, autosuspend is not the cause - say so and we will"
    say "look at the hub, the cable, or the driver instead."
}

do_install() {
    head_ "Writing a udev rule"

    local rules=() path name vid pid seen=""
    while IFS=$'\t' read -r path name; do
        [ -n "$path" ] || continue
        vid="$(read_attr "$path" idVendor)"
        pid="$(read_attr "$path" idProduct)"
        [ -n "$vid" ] && [ -n "$pid" ] || continue

        # One rule per vendor/product, however many input devices it exposes.
        case " $seen " in *" $vid:$pid "*) continue ;; esac
        seen="$seen $vid:$pid"

        rules+=("# ${name}")
        rules+=("ACTION==\"add\", SUBSYSTEM==\"usb\", ATTR{idVendor}==\"${vid}\", ATTR{idProduct}==\"${pid}\", TEST==\"power/control\", ATTR{power/control}=\"on\"")

        local hub hub_vid hub_pid
        hub="$(parent_hub "$path")"
        if [ -n "$hub" ]; then
            hub_vid="$(read_attr "$hub" idVendor)"
            hub_pid="$(read_attr "$hub" idProduct)"
            if [ -n "$hub_vid" ] && [ -n "$hub_pid" ]; then
                case " $seen " in *" $hub_vid:$hub_pid "*) ;; *)
                    seen="$seen $hub_vid:$hub_pid"
                    rules+=("# the hub it is plugged into")
                    rules+=("ACTION==\"add\", SUBSYSTEM==\"usb\", ATTR{idVendor}==\"${hub_vid}\", ATTR{idProduct}==\"${hub_pid}\", TEST==\"power/control\", ATTR{power/control}=\"on\"")
                ;; esac
            fi
        fi
    done < <(find_touch_devices)

    if [ "${#rules[@]}" = "0" ]; then
        bad "No touch devices found, so there is nothing to write a rule for."
        say "Plug the screen in and run this again."
        return 1
    fi

    say "This will write ${BOLD}${RULE_FILE}${RESET}:"
    say ""
    printf '    %s\n' "# Keeps a USB touchscreen from being autosuspended."
    printf '    %s\n' "${rules[@]}"
    say ""

    printf 'Write it? [y/N] '
    local answer
    read -r answer
    case "$answer" in
        [yY]|[yY][eE][sS]) ;;
        *) say "Left alone."; return 0 ;;
    esac

    {
        printf '%s\n' "# Keeps a USB touchscreen from being autosuspended."
        printf '%s\n' "# Written by touchscreen-keepalive.sh on $(date -Iseconds)."
        printf '%s\n' "${rules[@]}"
    } | sudo tee "$RULE_FILE" >/dev/null || {
        bad "Could not write $RULE_FILE"
        return 1
    }

    good "Written."
    sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=usb
    say ""

    # The rule fires on 'add', so it does not touch what is already plugged in.
    do_wake >/dev/null 2>&1
    say "Applied to what is connected now, and it will apply on every plug-in"
    say "and every boot from here."
    say ""
    do_status
}

do_uninstall() {
    head_ "Removing the rule"
    if [ ! -f "$RULE_FILE" ]; then
        say "Nothing installed at $RULE_FILE."
        return 0
    fi
    sudo rm -f "$RULE_FILE" || { bad "Could not remove $RULE_FILE"; return 1; }
    sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=usb
    good "Removed. Autosuspend goes back to the kernel default after a reboot."
}

usage() {
    cat <<EOF
${BOLD}touchscreen-keepalive${RESET} - stop a USB touchscreen dying after idle

  $0                 show touch devices and their power settings
  $0 --wake          turn autosuspend off now, until reboot (proves the cause)
  $0 --install       write a udev rule so it survives reboots
  $0 --uninstall     remove that rule
  $0 --help          this

A touchscreen that only fails after a long idle, and comes back when
replugged, is USB autosuspend. Start with --wake: if the screen survives an
idle it would normally not, --install makes it permanent.
EOF
}

## ── main ─────────────────────────────────────────────────────────────────────

case "${1-}" in
    ""|--status|-s) do_status ;;   # read-only; needs nothing installed
    --wake|-w)      do_wake ;;
    --install|-i)   require udevadm systemd; do_install ;;
    --uninstall|-u) require udevadm systemd; do_uninstall ;;
    --help|-h)      usage ;;
    *)              bad "Unknown option: $1"; say ""; usage; exit 2 ;;
esac
