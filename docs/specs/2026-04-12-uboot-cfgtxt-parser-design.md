# U-Boot Raspberry Pi config.txt Parser

## Context

The pxeboot firmware emulates the VideoCore GPU bootloader's PXE boot
sequence. It currently uses U-Boot's `env import -t` to parse config.txt,
which only handles `key=value` lines. This misses:

- **Conditional sections** (`[pi4]`, `[all]`, `[none]`, etc.) that real RPi
  OS config.txt files rely on for model-specific settings.
- **Space-separated directives** like `initramfs kernel.img followkernel`.
- **Multi-value keys** like `dtoverlay=` that appear multiple times.
- **Include directives** that reference other config files.
- **Device state filters** like `[EDID=...]` and `[gpio2=1]`.

A proper parser enables the firmware to boot directly from unmodified RPi OS
boot directories without manual config.txt editing.

## Design

### File structure

The patch adds three new files to U-Boot and modifies four existing files:

| New file | Purpose |
|----------|---------|
| `lib/rpi_cfgtxt.c` | Parser library (~400-500 lines) |
| `include/rpi_cfgtxt.h` | Public API header (~40 lines) |
| `cmd/cfgtxt.c` | Command wrapper (~80 lines) |

| Modified file | Change |
|---------------|--------|
| `cmd/Kconfig` | Add `CONFIG_CMD_CFGTXT` entry |
| `cmd/Makefile` | Add `cfgtxt.o` |
| `lib/Makefile` | Add `rpi_cfgtxt.o` |
| `board/raspberrypi/rpi/pxeboot.env` | Use `cfgtxt import` instead of `env import -t` |

### Kconfig

```kconfig
config CMD_CFGTXT
    bool "cfgtxt - Raspberry Pi config.txt parser"
    depends on ARCH_BCM283X || SANDBOX
    help
      Parse Raspberry Pi config.txt files and set environment
      variables. Handles conditional sections ([pi4], [all], etc.),
      key=value assignments, multi-value keys (dtoverlay, dtparam),
      and space-separated directives (initramfs, include).
      Compatible with VideoCore GPU bootloader config.txt format.
```

### Command interface

```
cfgtxt import <addr> <size> [<model>]
    Parse config.txt buffer at <addr> (hex), <size> bytes.
    Applies conditional section filtering for <model>.
    If <model> is omitted, auto-detects from board revision.
    Sets rpi_cfg_* environment variables for all parsed directives.

cfgtxt clear
    Remove all rpi_cfg_* environment variables.
```

### Parser library API

```c
/* Board model identifiers for conditional section evaluation.
 * Each enum value represents a specific board; the model matching
 * table (below) defines which config.txt section filters apply. */
enum rpi_board_model {
    RPI_MODEL_PI0,       /* Pi Zero, Zero W */
    RPI_MODEL_PI02,      /* Pi Zero 2 W */
    RPI_MODEL_PI1,       /* Pi 1A, 1A+, 1B, 1B+ */
    RPI_MODEL_CM1,       /* Compute Module 1 */
    RPI_MODEL_PI2,       /* Pi 2B */
    RPI_MODEL_PI3,       /* Pi 3B, 3B+, 3A+ */
    RPI_MODEL_CM3,       /* Compute Module 3, 3+ */
    RPI_MODEL_PI4,       /* Pi 4B */
    RPI_MODEL_PI400,     /* Pi 400 */
    RPI_MODEL_CM4,       /* Compute Module 4, 4S */
    RPI_MODEL_PI5,       /* Pi 5 */
    RPI_MODEL_PI500,     /* Pi 500 */
    RPI_MODEL_CM5,       /* Compute Module 5 */
};

/* Callback for include directives.
 * Called when the parser encounters "include <filename>".
 * The callback should load the file and call rpi_cfgtxt_parse()
 * recursively. Returns 0 on success, -errno on failure. */
typedef int (*cfgtxt_include_fn)(const char *filename, void *ctx);

#define RPI_CFGTXT_MAX_INCLUDE_DEPTH 8
#define RPI_CFGTXT_MAX_LINE_LEN     512

/* Parser options */
struct rpi_cfgtxt_opts {
    enum rpi_board_model model;    /* Board model for section filtering */
    u64 serial;                    /* Board serial for [serial=] filter (64-bit for Pi 5) */
    int boot_partition;            /* Boot partition (1-4 for MBR, 0=default) */
    bool tryboot;                  /* Tryboot flag for [tryboot] filter */
    int include_depth;             /* Current include nesting depth (caller sets to 0) */
    cfgtxt_include_fn include_cb;  /* Callback for include directives (NULL to skip) */
    void *include_ctx;             /* Opaque context for include callback */
};

/* Parse a config.txt buffer and set rpi_cfg_* environment variables.
 * The buffer is treated as read-only; the parser copies lines
 * internally for tokenization (max RPI_CFGTXT_MAX_LINE_LEN per line,
 * longer lines are truncated). Handles both LF and CRLF line endings.
 * Returns 0 on success, -errno on failure. */
int rpi_cfgtxt_parse(const char *buf, size_t size,
                     const struct rpi_cfgtxt_opts *opts);

/* Remove all rpi_cfg_* environment variables.
 * Returns number of variables removed. */
int rpi_cfgtxt_clear(void);

/* Convert model name string to enum. Returns -1 if unknown.
 * Accepts both short forms ("pi4b", "cm4") and section filter
 * names ("pi4"). See string-to-enum mapping table below. */
int rpi_cfgtxt_model_from_str(const char *name);

/* Return static, null-terminated list of section filter names
 * that match a given model (e.g., pi4b → {"pi4", NULL}). */
const char * const *rpi_cfgtxt_model_filters(enum rpi_board_model model);
```

**String-to-enum mapping** (`rpi_cfgtxt_model_from_str`):

| Input string(s) | Enum value |
|------------------|------------|
| `"pi0"`, `"pi0w"` | `RPI_MODEL_PI0` |
| `"pi02"`, `"pi02w"` | `RPI_MODEL_PI02` |
| `"pi1"`, `"pi1a"`, `"pi1b"` | `RPI_MODEL_PI1` |
| `"cm1"` | `RPI_MODEL_CM1` |
| `"pi2"`, `"pi2b"` | `RPI_MODEL_PI2` |
| `"pi3"`, `"pi3b"`, `"pi3b+"`, `"pi3a+"` | `RPI_MODEL_PI3` |
| `"cm3"`, `"cm3+"` | `RPI_MODEL_CM3` |
| `"pi4"`, `"pi4b"` | `RPI_MODEL_PI4` |
| `"pi400"` | `RPI_MODEL_PI400` |
| `"cm4"`, `"cm4s"` | `RPI_MODEL_CM4` |
| `"pi5"` | `RPI_MODEL_PI5` |
| `"pi500"` | `RPI_MODEL_PI500` |
| `"cm5"` | `RPI_MODEL_CM5` |

### Conditional section handling

The parser maintains filter state for each filter type independently.
A config.txt line is accepted only when ALL active filters pass (AND
logic) and the `[none]` blocker is not set.

**Filter types and evaluation:**

| Filter | Syntax | Evaluation |
|--------|--------|------------|
| Model | `[pi4]`, `[cm4]`, `[pi400]` | Match against `opts->model` using filter table |
| Serial | `[serial=0xDEADBEEF]` | Match against `opts->serial` |
| Partition | `[partition=1]` | Match against `opts->boot_partition` |
| Tryboot | `[tryboot]` | Match against `opts->tryboot` |
| EDID | `[EDID=VSC-TD2220]` | Read display EDID via I2C/HDMI; see below |
| GPIO | `[gpio2=1]` | Read GPIO pin via DM GPIO API; see below |
| All | `[all]` | Reset ALL filters, accept everything |
| None | `[none]` | Block all lines until next section header |

**EDID filter evaluation:** The syntax is `[EDID=<monitor-name>]`. The
parser queries the display subsystem for the connected monitor's EDID
name string. If `CONFIG_VIDEO` is not enabled or no display is
connected, the filter evaluates to `FILTER_NOMATCH` (fail-safe). On
real hardware with a connected display, the EDID is read via the HDMI
I2C DDC channel.

**GPIO filter evaluation:** The syntax is `[gpio<pin>=<0|1>]` where
`<pin>` is the BCM GPIO number and `<0|1>` is the expected logic level.
The parser uses `dm_gpio_get_value()` from the DM GPIO API to read the
pin. If `CONFIG_BCM2835_GPIO` is not enabled, the filter evaluates to
`FILTER_NOMATCH` (fail-safe).

**Combination rules:**
- Different filter types combine with AND.
- Same filter type replaces the previous filter of that type.
- `[all]` clears all filters.

**Model matching table:**

| Enum value | Matching section filters |
|------------|-------------------------|
| `RPI_MODEL_PI0` | `[pi0]` |
| `RPI_MODEL_PI02` | `[pi0]`, `[pi02]` |
| `RPI_MODEL_PI1` | `[pi1]` |
| `RPI_MODEL_CM1` | `[pi1]`, `[cm1]` |
| `RPI_MODEL_PI2` | `[pi2]` |
| `RPI_MODEL_PI3` | `[pi3]` |
| `RPI_MODEL_CM3` | `[pi3]`, `[cm3]` |
| `RPI_MODEL_PI4` | `[pi4]` |
| `RPI_MODEL_PI400` | `[pi4]`, `[pi400]` |
| `RPI_MODEL_CM4` | `[pi4]`, `[cm4]` |
| `RPI_MODEL_PI5` | `[pi5]` |
| `RPI_MODEL_PI500` | `[pi5]`, `[pi500]` |
| `RPI_MODEL_CM5` | `[pi5]`, `[cm5]` |

**Auto-detection:** When `<model>` is omitted from the command, the
parser reads the board revision from the VideoCore mailbox
(`BCM2835_MBOX_TAG_GET_BOARD_REV`) and maps it to the enum using the
same revision table as U-Boot's `board/raspberrypi/rpi/rpi.c`.

### Directive parsing

**Simple key=value** (last-wins semantics):

```
kernel=kernel8.img  →  env_set("rpi_cfg_kernel", "kernel8.img")
arm_64bit=1         →  env_set("rpi_cfg_arm_64bit", "1")
enable_uart=1       →  env_set("rpi_cfg_enable_uart", "1")
gpu_mem=128         →  env_set("rpi_cfg_gpu_mem", "128")
key=                →  env_set("rpi_cfg_key", "")
key=a=b             →  env_set("rpi_cfg_key", "a=b")
```

**Multi-value keys** (accumulate with index):

```
dtoverlay=vc4-kms-v3d       →  rpi_cfg_dtoverlay_0=vc4-kms-v3d
dtoverlay=i2c-rtc,addr=0x68 →  rpi_cfg_dtoverlay_1=i2c-rtc,addr=0x68
                                rpi_cfg_dtoverlay_count=2

dtparam=i2c_arm=on          →  rpi_cfg_dtparam_0=i2c_arm=on
                                rpi_cfg_dtparam_count=1

gpio=0-27=ip,pu             →  rpi_cfg_gpio_0=0-27=ip,pu
                                rpi_cfg_gpio_count=1
```

Multi-value keys: `dtoverlay`, `dtparam`, `gpio`.

**Space-separated directives:**

```
initramfs initramfs8 followkernel
→  rpi_cfg_initramfs=initramfs8
   rpi_cfg_initramfs_addr=followkernel

initramfs initramfs8 0x02000000
→  rpi_cfg_initramfs=initramfs8
   rpi_cfg_initramfs_addr=0x02000000

initramfs initramfs8
→  rpi_cfg_initramfs=initramfs8
   rpi_cfg_initramfs_addr=followkernel    (default when addr omitted)

initramfs
→  (ignored — no filename argument)

include extra.txt
→  Triggers include_cb("extra.txt", include_ctx)
   If include_cb is NULL or depth >= RPI_CFGTXT_MAX_INCLUDE_DEPTH,
   the include is silently skipped.
```

The `followkernel` keyword means "place the initramfs immediately
after the kernel in memory". The pxeboot.env script interprets this
by using the kernel end address (kernel_addr_r + filesize).

### Complete directive table

All known VideoCore config.txt directives, grouped by category. Every
directive is parsed and stored as an `rpi_cfg_*` env var.

**Boot files:**
`kernel`, `device_tree`, `initramfs` (space-sep), `auto_initramfs`,
`cmdline`, `armstub`, `os_prefix`, `overlay_prefix`, `start_file`,
`fixup_file`, `boot_ramdisk`

**Boot behavior:**
`arm_64bit`, `enable_uart`, `disable_splash`, `boot_partition`,
`kernel_address`, `total_mem`, `sha256`, `tryboot_a_b`,
`boot_load_flags`, `os_check`, `bootloader_update`,
`kernel_watchdog_timeout`, `kernel_watchdog_partition`

**Device tree:**
`dtoverlay` (multi), `dtparam` (multi), `dtdebug`,
`device_tree_address`, `device_tree_end`,
`camera_auto_detect`, `display_auto_detect`

**GPIO:**
`gpio` (multi), `enable_jtag_gpio`

**Memory and performance:**
`gpu_mem`, `gpu_mem_256`, `gpu_mem_512`, `gpu_mem_1024`,
`arm_freq`, `gpu_freq`, `core_freq`, `h264_freq`, `isp_freq`,
`v3d_freq`, `hevc_freq`, `arm_boost`, `sdram_freq`,
`over_voltage`, `force_turbo`

**Display and audio:**
`hdmi_enable_4kp60`, `disable_audio_dither`, `audio_pwm_mode`,
`pwm_sample_bits`, `power_force_3v3_pwm`

**Security:**
`program_pubkey`, `revoke_devkey`, `program_jtag_lock`,
`program_rpiboot_gpio`, `eeprom_write_protect`

Unknown directives are stored as `rpi_cfg_<key>=<value>` to provide
forward compatibility with new VideoCore firmware releases.

### Line handling

- Both LF (`\n`) and CRLF (`\r\n`) line endings are accepted.
- Leading whitespace (spaces and tabs) is stripped from each line.
- Trailing whitespace is stripped.
- Inline comments are NOT supported (matching VideoCore behavior).
- Lines longer than `RPI_CFGTXT_MAX_LINE_LEN` (512 bytes) are truncated.
- Lines that do not contain `=` and are not a recognized space-separated
  directive (`initramfs`, `include`) are silently skipped.
- Whitespace around `=` is NOT stripped: `key = value` sets key `"key "` to
  `" value"`. This matches VideoCore behavior (spaces around `=` are not
  standard in config.txt).

### Reimport behavior

`cfgtxt import` does NOT clear existing `rpi_cfg_*` variables. If called
twice without `cfgtxt clear`, the second parse overwrites simple keys
(last-wins) but multi-value counters (`dtoverlay_count`, etc.) are
reset to 0 at the start of each `rpi_cfgtxt_parse()` call, so indexed
values are rebuilt from scratch. Callers should use `cfgtxt clear`
before `cfgtxt import` for clean state (as shown in the pxeboot.env
integration example).

### Parser state machine

```c
struct cfgtxt_state {
    /* Filter state */
    enum {
        FILTER_UNSET,   /* no filter of this type active */
        FILTER_MATCH,   /* filter active and matches */
        FILTER_NOMATCH, /* filter active but does not match */
    } model_filter, serial_filter, partition_filter,
      tryboot_filter, edid_filter, gpio_filter;
    bool none_active;   /* [none] blocks everything */

    /* Multi-value counters (reset to 0 at start of each parse) */
    int dtoverlay_count;
    int dtparam_count;
    int gpio_count;

    /* Line buffer for tokenization (avoids mutating input) */
    char line_buf[RPI_CFGTXT_MAX_LINE_LEN];

    /* Board identity */
    const struct rpi_cfgtxt_opts *opts;
};
```

Line acceptance: a line passes when none of the active filters are in
`FILTER_NOMATCH` state, and `none_active` is false. `FILTER_UNSET`
counts as passing.

### pxeboot.env integration

The source of truth is `ci/vc-boot-pi4b.env`, which the build process
copies to `board/raspberrypi/rpi/pxeboot.env` inside the U-Boot source
tree (referenced by `CONFIG_ENV_SOURCE_FILE="pxeboot"` in the defconfig).

**Before (current):**

```
vc_parse_config=
    echo "  Parsing config.txt ...";
    setenv _cfg_kernel;
    setenv _cfg_dtb;
    setenv _cfg_initrd;
    if env import -t ${scratch_addr} ${filesize} kernel device_tree initramfs; then
        if test -n "${kernel}"; then
            setenv _cfg_kernel ${kernel};
            ...
```

**After:**

```
vc_parse_config=
    echo "  Parsing config.txt ...";
    cfgtxt clear;
    cfgtxt import ${scratch_addr} ${filesize} pi4b;
    if test -n "${rpi_cfg_kernel}"; then
        echo "  config.txt: kernel=${rpi_cfg_kernel}";
    fi;
    if test -n "${rpi_cfg_device_tree}"; then
        echo "  config.txt: device_tree=${rpi_cfg_device_tree}";
    fi;
    if test -n "${rpi_cfg_initramfs}"; then
        echo "  config.txt: initramfs=${rpi_cfg_initramfs}";
    fi;
```

All references to `_cfg_kernel`, `_cfg_dtb`, `_cfg_initrd` change to
`rpi_cfg_kernel`, `rpi_cfg_device_tree`, `rpi_cfg_initramfs` throughout
the env script.

### Patch delivery

The patch is added to `ci/qemu-patches/` as:

```
0019-cmd-add-Raspberry-Pi-config.txt-parser.patch
```

Applied during the U-Boot build alongside the existing 18 GENET/UART
patches. The defconfig adds `CONFIG_CMD_CFGTXT=y`.

## Testing

### Layer 1: C unit tests (in patch, `test/cmd/cfgtxt.c`)

Run in U-Boot sandbox mode. Each test writes a config.txt string into
memory, calls `cfgtxt import`, and asserts env var values.

| Test | Verifies |
|------|----------|
| `cfgtxt_test_basic_kv` | Simple key=value sets `rpi_cfg_<key>` |
| `cfgtxt_test_comments` | Lines starting with `#` are skipped |
| `cfgtxt_test_blank_lines` | Empty and whitespace-only lines are skipped |
| `cfgtxt_test_multi_value` | Multiple `dtoverlay=` lines produce `_0`, `_1`, `_count` |
| `cfgtxt_test_initramfs_space` | `initramfs file addr` sets two env vars |
| `cfgtxt_test_last_wins` | Duplicate key: last value wins |
| `cfgtxt_test_section_match` | `[pi4]` content applies for model `pi4b` |
| `cfgtxt_test_section_skip` | `[pi3]` content skipped for model `pi4b` |
| `cfgtxt_test_section_all_reset` | `[all]` resets filters |
| `cfgtxt_test_section_none` | `[none]` blocks lines until next section |
| `cfgtxt_test_section_and` | Different filter types combine with AND |
| `cfgtxt_test_section_replace` | Same filter type replaces previous |
| `cfgtxt_test_clear` | `cfgtxt clear` removes all `rpi_cfg_*` vars |
| `cfgtxt_test_empty_value` | `key=` sets empty string |
| `cfgtxt_test_value_with_equals` | `key=a=b` preserves `=` in value |
| `cfgtxt_test_malformed_line` | Lines without `=` and not a known directive are skipped |
| `cfgtxt_test_include_callback` | `include` triggers callback with filename |
| `cfgtxt_test_include_depth_limit` | Include at max depth is silently skipped |
| `cfgtxt_test_model_matching` | Each board model matches correct section filters |
| `cfgtxt_test_crlf_line_endings` | CRLF line endings parsed correctly |
| `cfgtxt_test_leading_whitespace` | Leading spaces/tabs stripped |
| `cfgtxt_test_initramfs_no_addr` | `initramfs file` defaults addr to `followkernel` |
| `cfgtxt_test_initramfs_no_args` | `initramfs` alone is ignored |
| `cfgtxt_test_reimport_resets_counters` | Second import resets multi-value counters |
| `cfgtxt_test_long_line_truncation` | Lines > 512 bytes truncated gracefully |
| `cfgtxt_test_gpio_filter_read` | `[gpio2=1]` reads actual GPIO state |

### Layer 2: Python integration tests (in patch, `test/py/tests/test_cfgtxt.py`)

Boot sandbox U-Boot and test through console with `run_command()` and
`printenv` verification.

| Test | Verifies |
|------|----------|
| `test_basic_import` | Write data to memory, import, check printenv |
| `test_real_rpi_config` | Parse representative RPi OS config.txt |
| `test_clear_removes_all` | `cfgtxt clear` removes all `rpi_cfg_*` |
| `test_idempotent_reimport` | Import same data twice, same result |
| `test_section_filtering` | Only matching sections applied |
| `test_error_invalid_addr` | Error message on bad address |
| `test_error_zero_size` | Error message on zero size |

### Layer 3: QEMU end-to-end tests (this project)

**Test 1: Real RPi OS config.txt.**
Create a config.txt with `[pi4]` section, `dtoverlay=`, `enable_uart=1`.
Verify parsed env vars appear in serial output and boot completes.

**Test 2: Conditional section exclusion.**
Config.txt with `[pi3]` and `[pi4]` sections setting different kernels.
Verify only the `[pi4]` kernel is used.

**Test 3: initramfs space-separated directive.**
Config.txt with `initramfs initramfs8 followkernel`.
Verify `rpi_cfg_initramfs=initramfs8` and initrd loads.

**Test 4: Regression.**
Existing tests (`run-rpi-boot-test.py`, socket tests) pass unchanged.

**Test infrastructure:**
Add `printenv rpi_cfg_` to pxeboot.env after `cfgtxt import` to dump
parsed state to serial. Test script checks patterns:

```python
checks = [
    ...
    ("Config parsed",    "rpi_cfg_kernel=kernel8.img"),
    ("Gzip decompress",  "Decompressed kernel"),
    ...
]
```

## Risks

**U-Boot env size limit.** A config.txt with many directives could
generate 50+ env vars. The defconfig has `CONFIG_ENV_SIZE=0x4000`
(16 KB) which is sufficient for typical configs.

**GPIO/EDID query failures.** If the DM GPIO driver or display
subsystem is not available, the filter evaluation should fail safe
(treat as non-matching) rather than crash.

**Include recursion.** The `include` callback could recurse
indefinitely if config files include each other. The parser should
enforce a maximum include depth (e.g., 8 levels).

**Unknown directives.** New VideoCore firmware versions may add
directives. The parser stores all key=value lines as env vars, so
unknown directives are preserved automatically.

## Critical files

| File | Action |
|------|--------|
| `ci/qemu-patches/0019-cmd-add-...` | New patch file (all U-Boot changes) |
| `ci/rpi_4_qemu_pxeboot_defconfig` | Add `CONFIG_CMD_CFGTXT=y` |
| `ci/vc-boot-pi4b.env` | Replace `env import -t` with `cfgtxt import` |
| `run-rpi-pxeboot-test.py` | Add config parsing verification checks |
