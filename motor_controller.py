def home_all_axes():
    print("[motor mock] home_all_axes: 三轴机械原点回归")


def move_z_step(direction, dist):
    print(f"[motor mock] move_z_step: direction={direction}, dist={dist}")
    import time
    import gpiod

    ONE_PULSE_DISTANCE = 1 #一个脉冲让运动的距离，单位cm，需要更精确测量！1cm只是一个估计值
    PULSE_TIME = 0.02
    CHIP_NUMBER = "0"
    GPIO_CONFIG = {
        "Z_DIR": {"offset":1,"init":0},
        "Z_PULSE": {"offset":2,"init":0}
    }

    # 打开芯片
    chip = gpiod.Chip(CHIP_NUMBER, gpiod.Chip.OPEN_BY_NUMBER)

    # 获取所有线路
    lines = {}
    for name, cfg in GPIO_CONFIG.items():
        line = chip.get_line(cfg["offset"])
        line.request(
            consumer="multi_gpio",
            type=gpiod.LINE_REQ_DIR_OUT,
            default_vals=[cfg["init"]]
        )
        lines[name] = line

    try:
        
        Stop_this=0
        if direction=="UP":
            lines["Z_DIR"].set_value(1)
        else if direction == "DOWN":
            lines["Z_DIR"].set_value(0)
        else:
            print("move_z_step的第一个参数只能是UP或DOWN中的一个！")
            Stop_this=1
    #=================================================================
        if dist<=0:
            Stop_this=1
        else:
            Pulse_count=dist//ONE_PULSE_DISTANCE#去尾法，取大约
    #=================================================================
        if Stop_this==0:
            i=0
            while i<Pulse_count:
                lines["Z_PULSE"].set_value(1)
                time.sleep(PULSE_TIME)
                lines["Z_PULSE"].set_value(0)
                time.sleep(PULSE_TIME)
                i=i+1
    except KeyboardInterrupt:
        print("\n收到中断信号，准备退出...")

    finally:
        # 清理：全部拉低并释放
        for name, line in lines.items():
            line.set_value(0)
            line.release()
            print(f"已释放: {name}")
        chip.close()

#==============end of move_z_step=======================================================
def move_to_xy(x, y):
    print(f"[motor mock] move_to_xy: x={x}, y={y}")
