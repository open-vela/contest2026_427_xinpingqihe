/****************************************************************************
 * apps/examples/phywear/pw_bt.h
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * 蓝牙 host 栈的启动入口（B1）。
 ****************************************************************************/

#ifndef __APPS_EXAMPLES_PHYWEAR_PW_BT_H
#define __APPS_EXAMPLES_PHYWEAR_PW_BT_H

/* 起 zblue host 栈 + 注册 GATT/开广播（B1+B2）。
 *
 * **幂等**：重复调用直接返回 0，不会把 service 再注册一遍、也不会重开广播。
 * 返回 0 = 栈已就绪（含"本来就已经起来了"）。 */
int pw_bt_init(void);

/* 栈是否已经起来（1/0）。界面用它决定"要不要顺手把蓝牙打开"。 */
int pw_bt_is_up(void);

#endif /* __APPS_EXAMPLES_PHYWEAR_PW_BT_H */
