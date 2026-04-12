# U-Boot config.txt Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `cfgtxt` command to U-Boot that parses Raspberry Pi config.txt with full VideoCore parity, and integrate it into the pxeboot firmware.

**Architecture:** Parser library in `lib/rpi_cfgtxt.c` exposes `rpi_cfgtxt_parse()` which reads a config.txt buffer, evaluates conditional sections, and sets `rpi_cfg_*` environment variables. Thin command wrapper in `cmd/cfgtxt.c` provides the `cfgtxt import` and `cfgtxt clear` console commands. Changes are delivered as a git-format-patch applied during U-Boot build.

**Tech Stack:** C (U-Boot), U-Boot env API (`env_set`/`env_get`), DM GPIO API, git format-patch

**Spec:** `docs/specs/2026-04-12-uboot-cfgtxt-parser-design.md`

**Key convention:** All U-Boot source changes happen inside `test-images/u-boot/` (the checked-out U-Boot tree). After all code compiles and tests pass, a `git format-patch` generates a `.patch` file that gets stored in `ci/uboot-patches/` for the build system to apply.

---

## File Map

**New files (inside U-Boot tree at `test-images/u-boot/`):**

| File | Responsibility |
|------|---------------|
| `include/rpi_cfgtxt.h` | Public API: enums, structs, function declarations |
| `lib/rpi_cfgtxt.c` | Parser: line splitting, section filters, directive handling, env var setting |
| `cmd/cfgtxt.c` | Command wrapper: arg parsing, memory mapping, calls into lib |
| `test/cmd/cfgtxt.c` | C unit tests for sandbox mode |

**Modified files (inside U-Boot tree):**

| File | Change |
|------|--------|
| `cmd/Kconfig` | Add `CONFIG_CMD_CFGTXT` entry after `CMD_ECHO` |
| `cmd/Makefile` | Add `obj-$(CONFIG_CMD_CFGTXT) += cfgtxt.o` |
| `lib/Makefile` | Add `obj-$(CONFIG_CMD_CFGTXT) += rpi_cfgtxt.o` |

**Project files (in repo root):**

| File | Change |
|------|--------|
| `ci/uboot-patches/0001-cmd-add-...patch` | Generated patch (all U-Boot changes) |
| `ci/rpi_4_qemu_pxeboot_defconfig` | Add `CONFIG_CMD_CFGTXT=y` |
| `ci/vc-boot-pi4b.env` | Replace `env import -t` with `cfgtxt import` |
| `.github/workflows/build-qemu-packages.yml` | Apply U-Boot patches during build |
| `run-rpi-pxeboot-test.py` | Add config parsing verification |

---

### Task 1: Create the API header

**Files:**
- Create: `test-images/u-boot/include/rpi_cfgtxt.h`

- [ ] **Step 1: Write the header file**

```c
/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * Raspberry Pi config.txt parser
 *
 * Parses VideoCore GPU bootloader config.txt format including
 * conditional sections, key=value pairs, multi-value keys,
 * and space-separated directives.
 */

#ifndef __RPI_CFGTXT_H
#define __RPI_CFGTXT_H

#include <linux/types.h>

/* Board model identifiers for conditional section evaluation */
enum rpi_board_model {
	RPI_MODEL_PI0,
	RPI_MODEL_PI02,
	RPI_MODEL_PI1,
	RPI_MODEL_CM1,
	RPI_MODEL_PI2,
	RPI_MODEL_PI3,
	RPI_MODEL_CM3,
	RPI_MODEL_PI4,
	RPI_MODEL_PI400,
	RPI_MODEL_CM4,
	RPI_MODEL_PI5,
	RPI_MODEL_PI500,
	RPI_MODEL_CM5,
	RPI_MODEL_COUNT,
};

/* Include callback: called when parser encounters "include <file>".
 * Callback should load the file and call rpi_cfgtxt_parse() recursively.
 * Returns 0 on success, -errno on failure. */
typedef int (*cfgtxt_include_fn)(const char *filename, void *ctx);

#define RPI_CFGTXT_MAX_INCLUDE_DEPTH	8
#define RPI_CFGTXT_MAX_LINE_LEN		512
#define RPI_CFGTXT_ENV_PREFIX		"rpi_cfg_"

/* Parser options */
struct rpi_cfgtxt_opts {
	enum rpi_board_model model;
	u64 serial;
	int boot_partition;
	bool tryboot;
	int include_depth;
	cfgtxt_include_fn include_cb;
	void *include_ctx;
};

/**
 * rpi_cfgtxt_parse() - Parse config.txt and set rpi_cfg_* env vars
 *
 * @buf:  Read-only buffer containing config.txt content
 * @size: Size of buffer in bytes
 * @opts: Parser options (model, serial, callbacks, etc.)
 * Return: 0 on success, -errno on failure
 */
int rpi_cfgtxt_parse(const char *buf, size_t size,
		     const struct rpi_cfgtxt_opts *opts);

/**
 * rpi_cfgtxt_clear() - Remove all rpi_cfg_* environment variables
 *
 * Return: number of variables removed
 */
int rpi_cfgtxt_clear(void);

/**
 * rpi_cfgtxt_model_from_str() - Convert model name string to enum
 *
 * Accepts "pi4b", "pi4", "cm4", "pi400", etc.
 * Return: enum value, or -1 if unknown
 */
int rpi_cfgtxt_model_from_str(const char *name);

/**
 * rpi_cfgtxt_model_filters() - Get section filters for a model
 *
 * Return: static, null-terminated array of filter name strings
 *         (e.g., {"pi4", NULL} for RPI_MODEL_PI4)
 */
const char * const *rpi_cfgtxt_model_filters(enum rpi_board_model model);

#endif /* __RPI_CFGTXT_H */
```

- [ ] **Step 2: Commit**

```bash
cd test-images/u-boot
git add include/rpi_cfgtxt.h
git commit -m "rpi: add config.txt parser API header"
```

---

### Task 2: Implement model mapping tables

**Files:**
- Create: `test-images/u-boot/lib/rpi_cfgtxt.c` (first section)

- [ ] **Step 1: Write model mapping data and utility functions**

Create `lib/rpi_cfgtxt.c` with:
- SPDX header and includes
- `struct model_name_entry` mapping table: string name to enum (spec table on lines 129-145)
- `rpi_cfgtxt_model_from_str()`: iterate table, `strcmp()`, return enum or -1
- `model_filters` static arrays: one per model, listing matching section filter names (spec table lines 186-200)
- `rpi_cfgtxt_model_filters()`: index into array by enum, return pointer

Reference for the mapping:
- `"pi4"` and `"pi4b"` both map to `RPI_MODEL_PI4`
- `RPI_MODEL_PI4` matches section filters `{"pi4", NULL}`
- `RPI_MODEL_PI400` matches `{"pi4", "pi400", NULL}`
- `RPI_MODEL_CM4` matches `{"pi4", "cm4", NULL}`
- See full table in spec lines 186-200

- [ ] **Step 2: Compile check**

```bash
cd test-images/u-boot
# Add to lib/Makefile: obj-$(CONFIG_CMD_CFGTXT) += rpi_cfgtxt.o
# Temporarily enable in .config
make -j$(nproc) CROSS_COMPILE=aarch64-linux-gnu- lib/rpi_cfgtxt.o
```

- [ ] **Step 3: Commit**

```bash
git add lib/rpi_cfgtxt.c
git commit -m "rpi: add config.txt model mapping tables"
```

---

### Task 3: Implement line parser and filter state machine

**Files:**
- Modify: `test-images/u-boot/lib/rpi_cfgtxt.c`

- [ ] **Step 1: Add filter state struct and line acceptance logic**

Add to `rpi_cfgtxt.c`:

```c
enum filter_state {
	FILTER_UNSET,
	FILTER_MATCH,
	FILTER_NOMATCH,
};

struct cfgtxt_state {
	enum filter_state model_filter;
	enum filter_state serial_filter;
	enum filter_state partition_filter;
	enum filter_state tryboot_filter;
	enum filter_state edid_filter;
	enum filter_state gpio_filter;
	bool none_active;

	int dtoverlay_count;
	int dtparam_count;
	int gpio_count;

	char line_buf[RPI_CFGTXT_MAX_LINE_LEN];
	const struct rpi_cfgtxt_opts *opts;
};
```

- [ ] **Step 2: Implement `line_accepted()`**

Returns true when no filter is `FILTER_NOMATCH` and `none_active` is false.

- [ ] **Step 3: Implement `eval_section_header()`**

Parses `[pi4]`, `[all]`, `[none]`, `[serial=0x...]`, `[partition=N]`, `[tryboot]`, `[EDID=...]`, `[gpio<N>=<0|1>]`. Updates the appropriate filter in `cfgtxt_state`.

Key logic:
- `[all]` → set all filters to `FILTER_UNSET`, `none_active = false`
- `[none]` → `none_active = true`
- `[pi4]` → check if model's filter list contains `"pi4"`, set `model_filter` to MATCH or NOMATCH
- `[serial=0x...]` → parse hex, compare to `opts->serial`
- `[gpio<N>=<V>]` → parse pin number and value, call `dm_gpio_get_value()`, compare
- `[EDID=...]` → if `CONFIG_VIDEO` not set, `FILTER_NOMATCH`; otherwise query display

Same-type sections replace the previous filter of that type.

- [ ] **Step 4: Implement `copy_line()`**

Copies a line from the const input buffer into `state->line_buf`:
- Stops at `\n` or end of buffer
- Strips `\r` if CRLF
- Strips leading whitespace (spaces and tabs)
- Strips trailing whitespace
- Null-terminates
- Truncates at `RPI_CFGTXT_MAX_LINE_LEN - 1`
- Returns pointer to next line in input buffer

- [ ] **Step 5: Compile check**

```bash
cd test-images/u-boot
make -j$(nproc) CROSS_COMPILE=aarch64-linux-gnu- lib/rpi_cfgtxt.o
```

- [ ] **Step 6: Commit**

```bash
git add lib/rpi_cfgtxt.c
git commit -m "rpi: add config.txt line parser and filter state machine"
```

---

### Task 4: Implement directive handlers and main parse loop

**Files:**
- Modify: `test-images/u-boot/lib/rpi_cfgtxt.c`

- [ ] **Step 1: Implement `set_env_indexed()`**

Helper for multi-value keys. Given prefix `"rpi_cfg_dtoverlay"` and counter pointer, sets `rpi_cfg_dtoverlay_<N>=<value>` and increments counter. Also updates `rpi_cfg_dtoverlay_count`.

```c
static void set_env_indexed(const char *prefix, int *counter,
			    const char *value)
{
	char name[64];

	snprintf(name, sizeof(name), "%s_%d", prefix, *counter);
	env_set(name, value);
	(*counter)++;
	snprintf(name, sizeof(name), "%s_count", prefix);
	env_set_ulong(name, *counter);
}
```

- [ ] **Step 2: Implement `handle_directive()`**

Processes a single accepted line:

1. If starts with `#` → skip (comment)
2. If empty → skip
3. If starts with `[` → call `eval_section_header()`, return
4. If starts with `initramfs ` → parse space-separated: set `rpi_cfg_initramfs` and `rpi_cfg_initramfs_addr` (default `followkernel` if no addr)
5. If starts with `include ` → check depth limit, call `opts->include_cb` if available
6. If contains `=` → split at first `=`:
   - Key is `dtoverlay`, `dtparam`, or `gpio` → call `set_env_indexed()`
   - Otherwise → `env_set("rpi_cfg_<key>", value)` (last-wins)
7. Otherwise → skip (unrecognized line)

Multi-value keys list: `"dtoverlay"`, `"dtparam"`, `"gpio"`.

- [ ] **Step 3: Implement `rpi_cfgtxt_parse()`**

Main entry point:

```c
int rpi_cfgtxt_parse(const char *buf, size_t size,
		     const struct rpi_cfgtxt_opts *opts)
{
	struct cfgtxt_state state;
	const char *pos = buf;
	const char *end = buf + size;

	if (!buf || !size || !opts)
		return -EINVAL;

	memset(&state, 0, sizeof(state));
	state.opts = opts;
	/* All filters start as FILTER_UNSET (== 0) */

	while (pos < end) {
		pos = copy_line(pos, end, &state);
		if (!state.line_buf[0])
			continue;  /* blank line */
		if (state.line_buf[0] == '#')
			continue;  /* comment */
		if (state.line_buf[0] == '[') {
			eval_section_header(state.line_buf, &state);
			continue;
		}
		if (line_accepted(&state))
			handle_directive(state.line_buf, &state);
	}
	return 0;
}
```

- [ ] **Step 4: Implement `rpi_cfgtxt_clear()`**

Iterates environment variables, removes any starting with `rpi_cfg_`. Uses `env_get_char()` / hash table iteration via `hwalk_r()` or by trying known variable names. Simplest approach: call `env_set(name, NULL)` for each discovered `rpi_cfg_*` var using the hashtable walk API from `search.h`.

- [ ] **Step 5: Compile check**

```bash
cd test-images/u-boot
make -j$(nproc) CROSS_COMPILE=aarch64-linux-gnu- lib/rpi_cfgtxt.o
```

- [ ] **Step 6: Commit**

```bash
git add lib/rpi_cfgtxt.c
git commit -m "rpi: add config.txt directive handlers and parse loop"
```

---

### Task 5: Write the command wrapper

**Files:**
- Create: `test-images/u-boot/cmd/cfgtxt.c`

- [ ] **Step 1: Write the command implementation**

```c
// SPDX-License-Identifier: GPL-2.0+
/*
 * cfgtxt - Raspberry Pi config.txt parser command
 */

#include <command.h>
#include <mapmem.h>
#include <rpi_cfgtxt.h>

static int do_cfgtxt_import(struct cmd_tbl *cmdtp, int flag,
			    int argc, char *const argv[])
{
	struct rpi_cfgtxt_opts opts = {};
	unsigned long addr, size;
	const char *buf;
	int model;

	if (argc < 3)
		return CMD_RET_USAGE;

	addr = hextoul(argv[1], NULL);
	size = hextoul(argv[2], NULL);

	if (!size) {
		printf("## Error: size is zero\n");
		return CMD_RET_FAILURE;
	}

	if (argc >= 4) {
		model = rpi_cfgtxt_model_from_str(argv[3]);
		if (model < 0) {
			printf("## Error: unknown model '%s'\n", argv[3]);
			return CMD_RET_FAILURE;
		}
		opts.model = model;
	}
	/* else: opts.model = RPI_MODEL_PI0 (0); caller should specify */

	buf = map_sysmem(addr, size);
	if (!buf) {
		printf("## Error: cannot map 0x%lx\n", addr);
		return CMD_RET_FAILURE;
	}

	if (rpi_cfgtxt_parse(buf, size, &opts)) {
		unmap_sysmem(buf);
		return CMD_RET_FAILURE;
	}

	unmap_sysmem(buf);
	return CMD_RET_SUCCESS;
}

static int do_cfgtxt_clear(struct cmd_tbl *cmdtp, int flag,
			   int argc, char *const argv[])
{
	int removed = rpi_cfgtxt_clear();

	printf("Removed %d rpi_cfg_* variables\n", removed);
	return CMD_RET_SUCCESS;
}

static struct cmd_tbl cmd_cfgtxt_sub[] = {
	U_BOOT_CMD_MKENT(import, 5, 0, do_cfgtxt_import, "", ""),
	U_BOOT_CMD_MKENT(clear, 1, 0, do_cfgtxt_clear, "", ""),
};

static int do_cfgtxt(struct cmd_tbl *cmdtp, int flag,
		     int argc, char *const argv[])
{
	struct cmd_tbl *cp;

	if (argc < 2)
		return CMD_RET_USAGE;

	cp = find_cmd_tbl(argv[1], cmd_cfgtxt_sub,
			  ARRAY_SIZE(cmd_cfgtxt_sub));
	if (!cp)
		return CMD_RET_USAGE;

	return cp->cmd(cmdtp, flag, argc - 1, argv + 1);
}

U_BOOT_CMD(
	cfgtxt, 5, 0, do_cfgtxt,
	"Raspberry Pi config.txt parser",
	"import <addr> <size> [<model>] - parse config.txt, set rpi_cfg_* vars\n"
	"cfgtxt clear                   - remove all rpi_cfg_* variables\n"
	"\n"
	"Models: pi0, pi02, pi1, cm1, pi2, pi3, cm3, pi4, pi4b,\n"
	"        pi400, cm4, pi5, pi500, cm5"
);
```

- [ ] **Step 2: Add to cmd/Makefile**

After the `obj-$(CONFIG_CMD_ECHO)` line, add:

```makefile
obj-$(CONFIG_CMD_CFGTXT) += cfgtxt.o
```

- [ ] **Step 3: Add Kconfig entry**

In `cmd/Kconfig`, after the `config CMD_ECHO` block, add:

```kconfig
config CMD_CFGTXT
	bool "cfgtxt - Raspberry Pi config.txt parser"
	depends on ARCH_BCM283X || SANDBOX
	help
	  Parse Raspberry Pi config.txt files and set rpi_cfg_*
	  environment variables. Handles conditional sections,
	  multi-value keys (dtoverlay, dtparam), and directives
	  like initramfs. Full VideoCore GPU bootloader parity.
```

- [ ] **Step 4: Add to lib/Makefile**

After a suitable line (e.g., near gzip), add:

```makefile
obj-$(CONFIG_CMD_CFGTXT) += rpi_cfgtxt.o
```

- [ ] **Step 5: Enable in defconfig and compile**

```bash
cd test-images/u-boot
echo "CONFIG_CMD_CFGTXT=y" >> configs/rpi_4_qemu_pxeboot_defconfig
make rpi_4_qemu_pxeboot_defconfig
make -j$(nproc) CROSS_COMPILE=aarch64-linux-gnu-
```

Expected: clean build producing `u-boot.bin`.

- [ ] **Step 6: Commit**

```bash
git add cmd/cfgtxt.c cmd/Kconfig cmd/Makefile lib/Makefile
git commit -m "rpi: add cfgtxt command wrapper and build integration"
```

---

### Task 6: Write C unit tests

**Files:**
- Create: `test-images/u-boot/test/cmd/cfgtxt.c`

- [ ] **Step 1: Write core test infrastructure and basic tests**

Each test: write config.txt string to memory, call `run_command("cfgtxt import ...")`, check env vars with `ut_asserteq_str(expected, env_get("rpi_cfg_..."))`.

Tests to implement (see spec lines 420-445 for full list):

```c
// SPDX-License-Identifier: GPL-2.0+

#include <command.h>
#include <env.h>
#include <mapmem.h>
#include <rpi_cfgtxt.h>
#include <test/lib.h>
#include <test/test.h>
#include <test/ut.h>

/* Helper: write string to mapped memory and run cfgtxt import */
static int cfgtxt_import(struct unit_test_state *uts,
			 const char *cfg, const char *model)
{
	u8 *buf = map_sysmem(0x1000, RPI_CFGTXT_MAX_LINE_LEN * 10);
	size_t len = strlen(cfg);
	char cmd[128];

	memcpy(buf, cfg, len);
	run_command("cfgtxt clear", 0);
	snprintf(cmd, sizeof(cmd), "cfgtxt import 1000 %lx %s",
		 (unsigned long)len, model ? model : "pi4b");
	run_command(cmd, 0);
	unmap_sysmem(buf);
	return 0;
}

static int cfgtxt_test_basic_kv(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "kernel=kernel8.img\narm_64bit=1\n", "pi4b");
	ut_asserteq_str("kernel8.img", env_get("rpi_cfg_kernel"));
	ut_asserteq_str("1", env_get("rpi_cfg_arm_64bit"));
	return 0;
}
LIB_TEST(cfgtxt_test_basic_kv, 0);

static int cfgtxt_test_comments(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "# comment\nkernel=k8.img\n# another\n", "pi4b");
	ut_asserteq_str("k8.img", env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_comments, 0);

static int cfgtxt_test_blank_lines(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "\n\nkernel=k8.img\n\n", "pi4b");
	ut_asserteq_str("k8.img", env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_blank_lines, 0);

static int cfgtxt_test_last_wins(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "kernel=first\nkernel=second\n", "pi4b");
	ut_asserteq_str("second", env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_last_wins, 0);

static int cfgtxt_test_empty_value(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "kernel=\n", "pi4b");
	ut_asserteq_str("", env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_empty_value, 0);

static int cfgtxt_test_value_with_equals(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "dtparam=i2c_arm=on\n", "pi4b");
	ut_asserteq_str("i2c_arm=on", env_get("rpi_cfg_dtparam_0"));
	return 0;
}
LIB_TEST(cfgtxt_test_value_with_equals, 0);

static int cfgtxt_test_multi_value(struct unit_test_state *uts)
{
	cfgtxt_import(uts,
		"dtoverlay=vc4-kms-v3d\ndtoverlay=i2c-rtc\n", "pi4b");
	ut_asserteq_str("vc4-kms-v3d", env_get("rpi_cfg_dtoverlay_0"));
	ut_asserteq_str("i2c-rtc", env_get("rpi_cfg_dtoverlay_1"));
	ut_asserteq_str("2", env_get("rpi_cfg_dtoverlay_count"));
	return 0;
}
LIB_TEST(cfgtxt_test_multi_value, 0);

static int cfgtxt_test_initramfs_space(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "initramfs initramfs8 followkernel\n", "pi4b");
	ut_asserteq_str("initramfs8", env_get("rpi_cfg_initramfs"));
	ut_asserteq_str("followkernel",
			env_get("rpi_cfg_initramfs_addr"));
	return 0;
}
LIB_TEST(cfgtxt_test_initramfs_space, 0);

static int cfgtxt_test_initramfs_no_addr(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "initramfs initramfs8\n", "pi4b");
	ut_asserteq_str("initramfs8", env_get("rpi_cfg_initramfs"));
	ut_asserteq_str("followkernel",
			env_get("rpi_cfg_initramfs_addr"));
	return 0;
}
LIB_TEST(cfgtxt_test_initramfs_no_addr, 0);

static int cfgtxt_test_section_match(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "[pi4]\nkernel=matched\n", "pi4b");
	ut_asserteq_str("matched", env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_section_match, 0);

static int cfgtxt_test_section_skip(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "[pi3]\nkernel=wrong\n", "pi4b");
	ut_assertnull(env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_section_skip, 0);

static int cfgtxt_test_section_all_reset(struct unit_test_state *uts)
{
	cfgtxt_import(uts,
		"[pi3]\nkernel=wrong\n[all]\nkernel=right\n", "pi4b");
	ut_asserteq_str("right", env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_section_all_reset, 0);

static int cfgtxt_test_section_none(struct unit_test_state *uts)
{
	cfgtxt_import(uts,
		"[none]\nkernel=blocked\n[all]\nkernel=ok\n", "pi4b");
	ut_asserteq_str("ok", env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_section_none, 0);

static int cfgtxt_test_section_replace(struct unit_test_state *uts)
{
	/* [pi4] then [pi3] replaces model filter -> pi3 only */
	cfgtxt_import(uts,
		"[pi4]\n[pi3]\nkernel=pi3only\n", "pi4b");
	ut_assertnull(env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_section_replace, 0);

static int cfgtxt_test_crlf(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "kernel=k8.img\r\narm_64bit=1\r\n", "pi4b");
	ut_asserteq_str("k8.img", env_get("rpi_cfg_kernel"));
	ut_asserteq_str("1", env_get("rpi_cfg_arm_64bit"));
	return 0;
}
LIB_TEST(cfgtxt_test_crlf, 0);

static int cfgtxt_test_leading_whitespace(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "  kernel=k8.img\n\tarm_64bit=1\n", "pi4b");
	ut_asserteq_str("k8.img", env_get("rpi_cfg_kernel"));
	ut_asserteq_str("1", env_get("rpi_cfg_arm_64bit"));
	return 0;
}
LIB_TEST(cfgtxt_test_leading_whitespace, 0);

static int cfgtxt_test_clear(struct unit_test_state *uts)
{
	cfgtxt_import(uts, "kernel=k8.img\n", "pi4b");
	ut_asserteq_str("k8.img", env_get("rpi_cfg_kernel"));
	run_command("cfgtxt clear", 0);
	ut_assertnull(env_get("rpi_cfg_kernel"));
	return 0;
}
LIB_TEST(cfgtxt_test_clear, 0);

static int cfgtxt_test_model_matching(struct unit_test_state *uts)
{
	const char * const *filters;

	/* pi4b matches [pi4] */
	ut_asserteq(RPI_MODEL_PI4,
		     rpi_cfgtxt_model_from_str("pi4b"));
	filters = rpi_cfgtxt_model_filters(RPI_MODEL_PI4);
	ut_asserteq_str("pi4", filters[0]);
	ut_assertnull(filters[1]);

	/* pi400 matches [pi4] and [pi400] */
	ut_asserteq(RPI_MODEL_PI400,
		     rpi_cfgtxt_model_from_str("pi400"));
	filters = rpi_cfgtxt_model_filters(RPI_MODEL_PI400);
	ut_asserteq_str("pi4", filters[0]);
	ut_asserteq_str("pi400", filters[1]);
	ut_assertnull(filters[2]);

	return 0;
}
LIB_TEST(cfgtxt_test_model_matching, 0);
```

- [ ] **Step 2: Add test to build**

Add to `test/cmd/Makefile`:
```makefile
obj-$(CONFIG_CMD_CFGTXT) += cfgtxt.o
```

- [ ] **Step 3: Compile check**

```bash
cd test-images/u-boot
make -j$(nproc) CROSS_COMPILE=aarch64-linux-gnu-
```

- [ ] **Step 4: Commit**

```bash
git add test/cmd/cfgtxt.c test/cmd/Makefile
git commit -m "test: add unit tests for cfgtxt config.txt parser"
```

---

### Task 7: Generate the U-Boot patch

**Files:**
- Create: `ci/uboot-patches/0001-cmd-add-Raspberry-Pi-config.txt-parser.patch`

- [ ] **Step 1: Generate patch from U-Boot commits**

```bash
cd test-images/u-boot
# Find the base commit (before our changes)
BASE=$(git log --oneline | grep -v "rpi:" | grep -v "test:" | head -1 | cut -d' ' -f1)
git format-patch ${BASE}..HEAD --stdout > ../../ci/uboot-patches/0001-cmd-add-Raspberry-Pi-config.txt-parser.patch
```

If the commits were made incrementally, squash them into one patch first, or use `git format-patch` with the appropriate range.

- [ ] **Step 2: Verify patch applies cleanly**

```bash
cd /tmp
git clone --depth=100 https://github.com/u-boot/u-boot.git uboot-test
cd uboot-test
git checkout 47e064f13171f15817aa1b22b04e309964b15c2c
git am /home/tim/github/fpgas-online/rpi-qemu/ci/uboot-patches/0001-cmd-add-Raspberry-Pi-config.txt-parser.patch
```

- [ ] **Step 3: Commit patch to project repo**

```bash
cd /home/tim/github/fpgas-online/rpi-qemu
git add ci/uboot-patches/
git commit -m "Add U-Boot patch: Raspberry Pi config.txt parser command"
```

---

### Task 8: Update build infrastructure

**Files:**
- Modify: `.github/workflows/build-qemu-packages.yml`
- Modify: `ci/rpi_4_qemu_pxeboot_defconfig`

- [ ] **Step 1: Add CONFIG_CMD_CFGTXT to defconfig**

In `ci/rpi_4_qemu_pxeboot_defconfig`, add:

```
CONFIG_CMD_CFGTXT=y
```

- [ ] **Step 2: Update build workflow to apply U-Boot patches**

In `.github/workflows/build-qemu-packages.yml`, in the `build-pxeboot` job, after the `git checkout` and `cp` lines for defconfig/env, add patch application:

```yaml
          # Apply U-Boot patches
          if [ -d ../../ci/uboot-patches ]; then
            for p in ../../ci/uboot-patches/*.patch; do
              git am "$p"
            done
          fi
```

This goes between the `cp` commands and the `make` command.

- [ ] **Step 3: Commit**

```bash
git add ci/rpi_4_qemu_pxeboot_defconfig .github/workflows/build-qemu-packages.yml
git commit -m "Build: apply U-Boot patches and enable CONFIG_CMD_CFGTXT"
```

---

### Task 9: Update vc-boot-pi4b.env to use cfgtxt

**Files:**
- Modify: `ci/vc-boot-pi4b.env`

- [ ] **Step 1: Replace vc_parse_config**

Replace the current `vc_parse_config` function (which uses `env import -t`) with:

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

- [ ] **Step 2: Update all _cfg_ variable references**

Throughout the env script, replace:
- `_cfg_kernel` → `rpi_cfg_kernel`
- `_cfg_dtb` → `rpi_cfg_device_tree`
- `_cfg_initrd` → `rpi_cfg_initramfs`

These appear in `vc_probe_kernel`, `vc_probe_dtb`, `vc_probe_initrd`, and `vc_boot_kernel`.

- [ ] **Step 3: Remove vc_parse_cmdline's selective import**

The `vc_parse_cmdline` function can remain as-is (it imports `bootargs` which is a standard U-Boot var, not an RPi config.txt directive).

- [ ] **Step 4: Remove stale cleanup lines**

Remove the `setenv kernel; setenv device_tree; setenv initramfs;` cleanup lines from `vc_parse_config` (no longer needed since `cfgtxt import` doesn't pollute the env namespace).

- [ ] **Step 5: Copy to local build tree**

```bash
cp ci/vc-boot-pi4b.env test-images/u-boot/board/raspberrypi/rpi/pxeboot.env
```

- [ ] **Step 6: Commit**

```bash
git add ci/vc-boot-pi4b.env
git commit -m "Firmware: use cfgtxt command for config.txt parsing

Replace env import -t with cfgtxt import for full VideoCore config.txt
parity. Env vars now use rpi_cfg_ prefix (rpi_cfg_kernel, etc.)."
```

---

### Task 10: Update test script

**Files:**
- Modify: `run-rpi-pxeboot-test.py`

- [ ] **Step 1: Update config.txt fixture for test**

In `setup_tftpboot()`, update the test config.txt to exercise conditional sections:

```python
config.write_text(
    "[pi4]\n"
    "kernel=kernel8.img\n"
    "[all]\n"
    "arm_64bit=1\n"
    "enable_uart=1\n"
)
```

- [ ] **Step 2: Add config parsing check**

Add to the `checks` list:

```python
("Config parsed",       "rpi_cfg_kernel=kernel8.img"),
```

- [ ] **Step 3: Add rpi_cfg_ to key output filter**

Add `"rpi_cfg_"` to the keyword list in the output summary loop so parsed config values show in test results.

- [ ] **Step 4: Commit**

```bash
git add run-rpi-pxeboot-test.py
git commit -m "Test: verify cfgtxt config.txt parsing in pxeboot test"
```

---

### Task 11: End-to-end verification

- [ ] **Step 1: Rebuild U-Boot with the patch**

```bash
cd test-images/u-boot
cp ../../ci/rpi_4_qemu_pxeboot_defconfig configs/rpi_4_qemu_pxeboot_defconfig
cp ../../ci/vc-boot-pi4b.env board/raspberrypi/rpi/pxeboot.env
make rpi_4_qemu_pxeboot_defconfig
make -j$(nproc) CROSS_COMPILE=aarch64-linux-gnu-
```

Expected: clean build.

- [ ] **Step 2: Run the pxeboot test**

```bash
cd /home/tim/github/fpgas-online/rpi-qemu
uv run run-rpi-pxeboot-test.py
```

Expected: all checks pass, including "Config parsed" and "Gzip decompress".

- [ ] **Step 3: Verify existing tests still pass**

```bash
uv run run-rpi-boot-test.py
uv run run-rpi-socket-boot-test.py
```

Expected: all pass (these use the interactive U-Boot path, not pxeboot).

- [ ] **Step 4: Test conditional section exclusion**

Temporarily modify the test config.txt to include a `[pi3]` section with a wrong kernel name. Verify it's ignored and the correct `[pi4]` kernel is used.

- [ ] **Step 5: Final commit if any fixes needed**
