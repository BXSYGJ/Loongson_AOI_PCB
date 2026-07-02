def home_all_axes():
    print("[motor mock] home_all_axes: 三轴机械原点回归")


def move_z_step(direction, dist):
    print(f"[motor mock] move_z_step: direction={direction}, dist={dist}")
    import time
    import gpiod

    ONE_PULSE_DISTANCE = 0.8
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
            Pulse_count=dist // ONE_PULSE_DISTANCE#去尾法，取大约
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
    import time
    import gpiod

# ========== 配置区域 ==========
    CHIP_NUMBER = "0"
    ONE_PULSE_DISTANCE = 0.8
    X_FULL_DIST=13.5
    Y_FULL_DIST=13.5
    PULSE_TIME = 0.02
    GPIO_CONFIG = {
        "X_DIR":    {"offset": 4,  "init": 0},
        "X_PULSE":  {"offset": 7,  "init": 0},
        "Y_DIR":   {"offset": 3, "init": 0},
        "Y_PULSE":  {"offset": 60, "init": 0},    
    }

    if(x>=13.5||x<=0||y>=13.5||y<=0):
        print("xy值不合法，应处在(0,13.5)")
        return 0
#获取当前x，y位置
        if not hasattr(move_to_xy,"x_now"):
            move_to_xy.x_now=0
        if not hasattr(move_to_xy,"y_now"):
            move_to_xy.y_now=0  
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
        delta_x = x - move_to_xy.x_now
        delta_y = y - move_to_xy.y_now
        if delta_x >= 0:
            lines["X_DIR"].set_value(1)
            dx = delta_x
            x_dir = 1
        if delta_x < 0:
            lines["X_DIR"].set_value(0)
            dx = -delta_x
            x_dir = -1
        if delta_y >= 0:
            lines["Y_DIR"].set_value(1)
            dy = delta_y
            y_dir = 1
        if delta_y < 0:
            lines["Y_DIR"].set_value(0)
            dy = -delta_y
            y_dir = -1
        Pulse_count_x = dx // ONE_PULSE_DISTANCE
        Pulse_count_y = dy // ONE_PULSE_DISTANCE
        
        move_to_xy.x_now = move_to_xy.x_now + (x_dir * Pulse_count_x * ONE_PULSE_DISTANCE)
        move_to_xy.y_now = move_to_xy.y_now + (y_dir * Pulse_count_y * ONE_PULSE_DISTANCE)
        
        i=0
        while i < Pulse_count_x:
            lines["X_PULSE"].set_value(1)
            time.sleep(PULSE_TIME)
            lines["X_PULSE"].set_value(0)
            time.sleep(PULSE_TIME)
            i = i + 1
        j=0
        while j < Pulse_count_y:
            lines["Y_PULSE"].set_value(1)
            time.sleep(PULSE_TIME)
            lines["Y_PULSE"].set_value(0)
            time.sleep(PULSE_TIME)
            j = j + 1

    except KeyboardInterrupt:
        print("\n收到中断信号，准备退出...")

    finally:
    # 清理：全部拉低并释放
        for name, line in lines.items():
            line.set_value(0)
            line.release()
        chip.close()