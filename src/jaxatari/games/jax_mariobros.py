# Team Mario Bros: Sai Aakaash, Sam Diehl, Jonas Rudolph, Harshith Salanke

import os
from functools import partial
from typing import NamedTuple, Tuple
import jax
import jax.numpy as jnp
import chex
import pygame
from jax import lax, debug
from gymnax.environments import spaces
from jaxatari.environment import JaxEnvironment, JAXAtariAction as Action

# --- Screen params---
SCREEN_WIDTH = 160
SCREEN_HEIGHT = 210
WINDOW_SCALE = 3

# --- Physik params ---
MOVE_SPEED = 0.5
ASCEND_VY = -1.0  # ↑ 2 px / frame
DESCEND_VY = 1.0  # ↓ 2 px / frame
ASCEND_FRAMES = 21 * 2  # 42 px tall jump (21 × 2)

# -------- Movement params ----------------------------------
movement_pattern = jnp.array([1, 1, 0, 1, 0, 1, 1, 0, 1, 1, 0], dtype=jnp.float32)
pat_len = movement_pattern.shape[0]

# -------- Break params ----------------------------------
BRAKE_DURATION = 10 * 2# in Frames
BRAKE_TOTAL_DISTANCE = 7.0 * 2# in Pixels
BRAKE_SPEED = jnp.array(BRAKE_TOTAL_DISTANCE / BRAKE_DURATION, dtype=jnp.float32)  # ≈ 0.7 px/frame

# --- Player params ---------------
PLAYER_SIZE = (9, 21)  # w, h
PLAYER_START_X, PLAYER_START_Y = 15, 140
PLAYER_RESPWAN_XY = jnp.array([78, 36], dtype=jnp.float32)

# --- Enemies params ---
ENEMY_SIZE = (8, 8)  # w, h
ENEMY_SPAWN_FRAMES = jnp.array([200, 0, 400])  # example delays for 3 enemies
ENEMY_MOVE = 8
ENEMY_SPAWN = jnp.array([jnp.array([5, 40]), jnp.array([130, 40])])
ENEMY_WEAK_FRAMES = 776

# --- Fireball params ---
FIREBALL_SIZE = (9, 14)
FIREBALL_MOVEMENT_Start = jnp.array([32, 32, 32, 24], dtype=jnp.float32)
FIREBALL_MOVEMENT = jnp.array([4, 6, 6], dtype=jnp.float32)
FIREBALL_RESTART = 120
FIREBALL_Y = jnp.array([175 - 28, 135 - 28, 95 - 28, 57 - 28])
FIREBALL_INIT_XY = jnp.array([145, FIREBALL_Y[1]])
# --- Object Colors ---
PLAYER_COLOR = jnp.array([0, 255, 0], dtype=jnp.uint8)
ENEMY_COLOR = jnp.array([255, 0, 0], dtype=jnp.uint8)
FIREBALL_COLOR = jnp.array([255, 255, 0], dtype=jnp.uint8)
PLATFORM_COLOR = jnp.array([228, 111, 111], dtype=jnp.uint8)
GROUND_COLOR = jnp.array([181, 83, 40], dtype=jnp.uint8)
POW_COLOR = jnp.array([201, 164, 74], dtype=jnp.uint8)

# --- Platform params---
PLATFORMS = jnp.array([
    [0, 175, 160, 23],  # Ground x, y, w, h
    [0, 57, 64, 3],  # 3.FLoor Left
    [96, 57, 68, 3],  # 3.Floor Right
    [31, 95, 97, 3],  # 2.Floor Middle
    [0, 95, 16, 3],  # 2.Floor Left
    [144, 95, 18, 3],  # 2.Floor Right
    [0, 135, 48, 3],  # 1.Floor Left
    [112, 135, 48, 3]  # 1.Floor Right
])
PLATFORMS_X = jnp.array([[160, 0], [48, 112], [128, 31], [64, 96]])
PLATFORMS_Y = jnp.array([175, 135, 195, 57])
# --- Pow_Block params ---
POW_BLOCK = jnp.array([[72, 141, 16, 7]])  # x, y, w, h

# Each number maps to 7-segment display (a,b,c,d,e,f,g)
DIGIT_SEGMENTS = jnp.array([
    [1,1,1,1,1,1,0],  # 0
    [0,1,1,0,0,0,0],  # 1
    [1,1,0,1,1,0,1],  # 2
    [1,1,1,1,0,0,1],  # 3
    [0,1,1,0,0,1,1],  # 4
    [1,0,1,1,0,1,1],  # 5
    [1,0,1,1,1,1,1],  # 6
    [1,1,1,0,0,0,0],  # 7
    [1,1,1,1,1,1,1],  # 8
    [1,1,1,1,0,1,1],  # 9
], dtype=jnp.int32)


class Fireball(NamedTuple):
    pos: jnp.ndarray
    start_pat: jnp.ndarray
    move_pat: jnp.ndarray
    count: chex.Array
    state: chex.Array
    dir: chex.Array

class new_Enemy(NamedTuple):
    enemy_pos: jnp.ndarray
    enemy_move: jnp.ndarray
    enemy_platform_idx: jnp.ndarray # -1 not spawned, 0 ground, 1 1.Floor etc.
    enemy_status: jnp.ndarray  # 0 not spawned, 1 spawned, 2 weak, 3 falling
    enemy_dir: jnp.ndarray
    weak_frames: jnp.ndarray
    enemy_spawn_idx: int

class Enemy(NamedTuple):
    enemy_pos: jnp.ndarray  # shape (N,2): x/y positions
    enemy_vel: jnp.ndarray  # shape (N,2): x/y velocities
    enemy_platform_idx: jnp.ndarray  # shape (N,): index of current platform the enemy is on
    enemy_timer: jnp.ndarray  # shape (N,): frame count until next patrol/teleport decision
    enemy_initial_sides: jnp.ndarray  # shape (N,): 0=spawned on left, 1=spawned on right
    enemy_delay_timer: jnp.ndarray  # shape (N,), counts frames until enemy starts moving
    enemy_init_positions: jnp.ndarray  # shape (N,2): initial spawn positions of enemies  <--- add this
    enemy_status: jnp.ndarray

class GameState(NamedTuple):  # Enemy movement
    enemy: new_Enemy
    pow_block_counter: int  # scalar: number of hits remaining on the POW block
    score: int
    fireball: Fireball
    game_over: bool

class PlayerState(NamedTuple):  # Player movement
    pos: jnp.ndarray
    vel: jnp.ndarray
    on_ground: bool
    jump_phase: chex.Array
    ascend_frames: chex.Array
    idx_right: chex.Array
    idx_left: chex.Array
    jump: chex.Array
    move: chex.Array
    jumpL: chex.Array
    jumpR: chex.Array
    last_dir: chex.Array
    brake_frames_left: chex.Array
    bumped_idx: chex.Array
    pow_bumped: chex.Array
    safe: bool


class MarioBrosState(NamedTuple):
    player: PlayerState
    game: GameState
    lives: chex.Array


class MarioBrosObservation(NamedTuple):  # Copied from jax_kangaroo.py ln.166-168
    player_x: chex.Array
    player_y: chex.Array


class MarioBrosInfo(NamedTuple):  # Copied from jax_kangaroo.py ln.186-187
    score: chex.Array

class MarioBrosConstants(NamedTuple):
    SCREEN_WIDTH: int = 160
    SCREEN_HEIGHT: int = 210


def check_collision(pos: jnp.ndarray, vel: jnp.ndarray, platforms: jnp.ndarray, pow_block: jnp.ndarray, pow_block_counter:int):
    x, y = pos
    vx, vy = vel
    w, h = PLAYER_SIZE

    left, right = x, x + w
    top, bottom = y, y + h

    # Platforms
    px, py, pw, ph = platforms[:, 0], platforms[:, 1], platforms[:, 2], platforms[:, 3]
    p_left, p_right = px, px + pw
    p_top, p_bottom = py, py + ph

    overlap_x = (right > p_left) & (left < p_right)
    overlap_y = (bottom > p_top) & (top < p_bottom)
    collided = overlap_x & overlap_y

    landed = collided & (vy > 0) & (bottom - vy <= p_top)
    bumped = collided & (vy < 0) & (top - vy >= p_bottom)

    # Landing and bump height adjustments
    landing_y = jnp.where(landed, p_top - h, jnp.inf)
    bumping_y = jnp.where(bumped, p_bottom, -jnp.inf)
    new_y_land = jnp.min(landing_y)
    new_y_bump = jnp.max(bumping_y)

    # POW block (only bump from below)
    pow_x, pow_y, pow_w, pow_h = pow_block[0]
    pow_left, pow_right = pow_x, pow_x + pow_w
    pow_top, pow_bottom = pow_y, pow_y + pow_h

    pow_overlap_x = (right > pow_left) & (left < pow_right)

    def compute_pow_bump(_):
        # only run when counter > 0
        return (
            pow_overlap_x &
            (vy < 0) &
            (top - vy >= pow_bottom) &
            (top <= pow_bottom)
        )
    
    # if counter ≤ 0, always False
    pow_bumped = lax.cond(
        pow_block_counter > 0,
        compute_pow_bump,
        lambda _: False,
        operand=None,
    )
    
    pow_bump_y = jnp.where(pow_bumped, pow_bottom, -jnp.inf)
    pow_y_new = jnp.max(pow_bump_y)

    # Calculate bumped_idx (index of bumped platform or -1 if none)
    bumped_indices = jnp.where(bumped, jnp.arange(len(bumped)), 1_000_000)
    min_idx = jnp.min(bumped_indices)
    bumped_idx = jnp.where(min_idx == 1_000_000, -1, min_idx)

    return (jnp.any(landed),
            jnp.any(bumped | pow_bumped),
            new_y_land,
            jnp.maximum(new_y_bump, pow_y_new),
            pow_bumped,
            bumped_idx)

def check_enemy_collision(player_pos, enemy_pos):
    # checks Player collision with enemys

    px, py = player_pos
    pw, ph = PLAYER_SIZE
    ex, ey = enemy_pos[:, 0], enemy_pos[:, 1]
    ew, eh = ENEMY_SIZE

    overlap_x = (px < ex + ew) & (px + pw > ex)
    overlap_y = (py < ey + eh) & (py + ph > ey)
    return jnp.any(overlap_x & overlap_y)


def fireball_step(fb:Fireball):
    def start(f: Fireball):
        arr = f.start_pat
        first_nonzero_index = jnp.argmax(arr != 0)
        arr = arr.at[first_nonzero_index].set(arr[first_nonzero_index] - 1)
        new_x = f.pos[0] + f.dir
        last_is_zero = arr[first_nonzero_index] == 0
    
        new_pos= jnp.array([jnp.where(arr[first_nonzero_index] == 0, new_x, f.pos[0]), f.pos[1]]) 
        new_state= jnp.where(last_is_zero, 1, 0)
        new_start_pat= jnp.where(last_is_zero, FIREBALL_MOVEMENT_Start, arr)
        return Fireball(
            pos= new_pos,
            start_pat= new_start_pat,
            move_pat= f.move_pat,
            count= f.count,
            state= new_state,
            dir= f.dir
        )
        
    def move(f: Fireball):
        arr = f.move_pat
        first_nonzero_index = jnp.argmax(arr != 0)
        arr = arr.at[first_nonzero_index].set(arr[first_nonzero_index] - 1)
        new_x = f.pos[0] + f.dir
        last_is_zero = arr[first_nonzero_index] == 0
        
        new_pos= jnp.array([jnp.where(arr[first_nonzero_index] == 0, new_x, f.pos[0]), f.pos[1]])
        new_state= jnp.where(new_pos[0]<4, 2, 1)
        new_move_pat= jnp.where(last_is_zero, FIREBALL_MOVEMENT, arr)
        return Fireball(
            pos= new_pos,
            start_pat= f.start_pat,
            move_pat= new_move_pat,
            count= f.count,
            state= new_state,
            dir= f.dir
        )
    def wait(f: Fireball):
        def stay(ff: Fireball):
            return Fireball(
            pos= ff.pos,
            start_pat= ff.start_pat,
            move_pat= ff.move_pat,
            count= ff.count - 1,
            state= ff.state,
            dir= ff.dir
        )
        def end(ff: Fireball):
            return Fireball(
            pos= FIREBALL_INIT_XY,
            start_pat= ff.start_pat,
            move_pat= ff.move_pat,
            count= 60,
            state= 0,
            dir= ff.dir
        )
        return lax.cond(f.count > 0, stay, end, f)
    return lax.switch(fb.state, [start, move, wait], fb)

@jax.jit
def check_enemy_spawn(enemy: new_Enemy) -> new_Enemy:

    # prüfen, ob eine Plattform idx == 3 existiert
    any_spawn = jnp.any(enemy.enemy_platform_idx == 3)
    jax.debug.print("Spawn? = {}", any_spawn)
    first_zero_index = jnp.argmax(enemy.enemy_status == 0)
    side = enemy.enemy_spawn_idx
    def do_spawn(_):
        return spawn_enemy(enemy, side, first_zero_index)
    new_enemy = lax.cond(any_spawn, lambda _: enemy, do_spawn, operand=None)
    return new_enemy

@jax.jit
def spawn_enemy(enemy:new_Enemy, side: int, idx: int) -> new_Enemy:
    # position updaten
    enemy_pos = enemy.enemy_pos.at[idx].set(ENEMY_SPAWN[side])
    # move pattern setzen
    enemy_move = enemy.enemy_move.at[idx].set(ENEMY_MOVE)
    # platform idx setzen
    platform_idx = enemy.enemy_platform_idx.at[idx].set(-1)
    # status setzen
    status = enemy.enemy_status.at[idx].set(3)
    # richtung setzen
    direction = enemy.enemy_dir.at[idx].set(jnp.where(side > 0, -1, 1))
    # weak frames zurücksetzen
    weak = enemy.weak_frames.at[idx].set(0)
    # spawn idx zurücksetzen (0 oder 1)
    spawn_idx = jnp.int32(jnp.where(enemy.enemy_spawn_idx > 0, 0, 1))

    return enemy._replace(
        enemy_pos=enemy_pos,
        enemy_move=enemy_move,
        enemy_platform_idx=platform_idx,
        enemy_status=status,
        enemy_dir=direction,
        weak_frames=weak,
        enemy_spawn_idx=spawn_idx
    )

@jax.jit
def check_enemy_fall(enemy: new_Enemy, idx: int) -> new_Enemy:
    x = enemy.enemy_pos[idx, 0]
    plat = enemy.enemy_platform_idx[idx]
    # falls nach rechts unterwegs:
    def fall_right(e: new_Enemy):
        cond = x > PLATFORMS_X[plat, 1]
        status = e.enemy_status.at[idx].set(jnp.where(cond, 3, e.enemy_status[idx]))
        return e._replace(enemy_status=status)
    # falls nach links unterwegs:
    def fall_left(e: new_Enemy):
        cond = x < PLATFORMS_X[plat, 0]
        status = e.enemy_status.at[idx].set(jnp.where(cond, 3, e.enemy_status[idx]))
        return e._replace(enemy_status=status)
    return lax.cond(enemy.enemy_dir[idx] > 0, fall_right, fall_left, enemy)

def spawned(idx: int, enemy: new_Enemy) -> new_Enemy:
    def not_strong(e: new_Enemy):
        def no_move(e2: new_Enemy):
            # horizontale Bewegung + ggf. Richtungswechsel + Wraparound
            x, y = e2.enemy_pos[idx]
            dir = e2.enemy_dir[idx]
            plat = e2.enemy_platform_idx[idx]

            screen_left = 0
            screen_right = SCREEN_WIDTH - ENEMY_SIZE[0]

            new_x = x + dir
            new_x_wrapped = jnp.where(new_x < screen_left, screen_right,
                            jnp.where(new_x > screen_right, screen_left, new_x))

            over_left = new_x_wrapped < PLATFORMS_X[plat, 0]
            over_right = new_x_wrapped > PLATFORMS_X[plat, 1]
            platform_end = over_left | over_right

            new_dir = jnp.where(platform_end, -dir, dir)

            new_pos = e2.enemy_pos.at[idx].set(jnp.array([new_x_wrapped, y]))
            new_enemy_dir = e2.enemy_dir.at[idx].set(new_dir)

            return e2._replace(enemy_pos=new_pos, enemy_dir=new_enemy_dir)

        return lax.cond(
            e.enemy_move[idx] == 0,
            no_move,
            lambda e2: e2._replace(enemy_move=e2.enemy_move.at[idx].set(e2.enemy_move[idx] - 1)),
            e
        )

    def strong_case(e: new_Enemy):
        wf = e.weak_frames[idx]

        def reset_strong(e2: new_Enemy):
            return e2._replace(enemy_status=e2.enemy_status.at[idx].set(1))

        def dec_weak(e2: new_Enemy):
            return e2._replace(weak_frames=e2.weak_frames.at[idx].set(wf - 1))

        return lax.cond(wf == 0, reset_strong, dec_weak, e)

    return lax.cond(enemy.enemy_status[idx] != 2, not_strong, strong_case, enemy)

@jax.jit
def new_enemy_step(idx: int, enemy: new_Enemy) -> new_Enemy:
    status = enemy.enemy_status[idx]

    def handle_fall(e: new_Enemy):
        y = e.enemy_pos[idx, 1]
        plat_idx = e.enemy_platform_idx[idx]

        has_plat = plat_idx >= 0
        curr_plat = jnp.where(has_plat, plat_idx, 4)
        next_plat_idx = jnp.where(has_plat, curr_plat - 1, 3)

        # 🐛 FIXED: use platform_y - enemy_height (not +8)
        target_y = PLATFORMS_Y[next_plat_idx] - ENEMY_SIZE[1]
        new_y = jnp.minimum(y + DESCEND_VY, target_y)
        e = e._replace(enemy_pos=e.enemy_pos.at[idx, 1].set(new_y))

        has_landed = (y + DESCEND_VY >= target_y)

        def landed(e2: new_Enemy):
            return e2._replace(
                enemy_move=e2.enemy_move.at[idx].set(ENEMY_MOVE),
                enemy_platform_idx=e2.enemy_platform_idx.at[idx].set(next_plat_idx),
                enemy_status=e2.enemy_status.at[idx].set(1)
            )

        return lax.cond(has_landed, landed, lambda x: x, e)

    def handle_non_fall(e: new_Enemy):
        return spawned(idx, e)

    return lax.cond(status == 3, handle_fall, handle_non_fall, enemy)

@jax.jit
def enemy_step(enemy: new_Enemy) -> new_Enemy:
    def step_with_fall(idx, e):
        e1 = new_enemy_step(idx, e)
        return check_enemy_fall(e1, idx)  # <--- jetzt aktiv nach jedem Schritt

    n = enemy.enemy_pos.shape[0]
    return lax.fori_loop(0, n, step_with_fall, enemy)


@jax.jit
def old_enemy_step(
    enemy_pos,
    enemy_vel,
    enemy_platform_idx,
    enemy_timer,
    platforms,
    initial_sides,
    active_mask,
    init_positions,
    enemy_status,   # added status
):
    ew, eh = ENEMY_SIZE
    pw = platforms[:, 2]
    enemy_init_platform_idx = jnp.array([1, 2, 1])  # same as initial enemy_platform_idx
    TELEPORT_DELAY_FRAMES = 5 * 60  # e.g. 5 seconds at 60 FPS; adjust as needed

    # Your per-enemy spawn delay array
    ENEMY_SPAWN_FRAMES = jnp.array([200, 0, 400])  # example, same length as enemies

    def step_enemy(pos, vel, p_idx, timer, side, i, status):
        x, y = pos
        vx, vy = vel
        # Handle dead (status == 3) enemy with respawn delay
        def dead_logic():
            invisible_pos = jnp.array([-1000.0, -1000.0])
            invisible_vel = jnp.array([0.0, 0.0])
            new_timer = timer + 1

            def do_respawn():
                new_pos, new_vel, new_p_idx, _, new_side, _ = teleport_up(side, i, enemy_init_platform_idx,
                                                                          init_positions)
                new_status = jnp.array(2, dtype=jnp.int32)  # recovering (active) status
                new_timer_respawn = 0
                return new_pos, new_vel, new_p_idx, new_timer_respawn, new_side, new_status

            def wait_respawn():
                # Invisible, increment timer, keep status 3 (dead)
                return invisible_pos, invisible_vel, p_idx, new_timer, side, status

            return jax.lax.cond(new_timer > ENEMY_SPAWN_FRAMES[i], do_respawn, wait_respawn)

        # If enemy is weak (status==1) or dead (status==3), velocity is zero
        is_weak_or_dead = (status == 1) | (status == 3)
        vx = jnp.where(is_weak_or_dead, 0.0, vx)
        vy = jnp.where(is_weak_or_dead, 0.0, vy)

        # Fix: If recovering from weak state (status == 2), resume movement
        recovering = status == 2
        vx = jnp.where(recovering & (vx == 0.0), jnp.where(side == 1, -0.5, 0.5), vx)

        plat = platforms[p_idx]
        plat_x, plat_y, plat_w, plat_h = plat
        plat_left = plat_x
        plat_right = plat_x + plat_w

        EPS = 0.5
        at_left_edge = x <= plat_left + EPS
        at_right_edge = x + ew >= plat_right - EPS
        at_edge = at_left_edge | at_right_edge

        # Check platform below at next x position (x + vx)
        def platform_below_y(pos_x):
            px, py, pw, ph = platforms[:, 0], platforms[:, 1], platforms[:, 2], platforms[:, 3]
            left = px
            right = px + pw
            supported_x = (pos_x + ew > left) & (pos_x < right)
            below_y = py > y
            candidates = jnp.where(supported_x & below_y, py, jnp.inf)
            return jnp.min(candidates)

        min_platform_below_y = platform_below_y(x + vx)
        has_platform_below = min_platform_below_y != jnp.inf

        def teleport_down(pos_x, vx, side, ew, eh, platforms, min_platform_below_y):
            same_y_mask = platforms[:, 1] == min_platform_below_y
            plat_lefts = platforms[:, 0]
            plat_rights = platforms[:, 0] + platforms[:, 2]
            inside_x_mask = (pos_x + ew > plat_lefts) & (pos_x < plat_rights)
            valid_mask = same_y_mask & inside_x_mask

            idx_below = jnp.argmax(valid_mask.astype(jnp.int32))  # pick first valid below

            new_pos_x = jnp.array(pos_x, dtype=jnp.float32)
            new_pos_y = jnp.array(platforms[idx_below, 1] - eh, dtype=jnp.float32)
            new_vx_down = vx
            new_timer = jnp.array(0, dtype=jnp.int32)
            new_side = jnp.array(side, dtype=jnp.int32)

            return (
                jnp.array([new_pos_x, new_pos_y]),
                jnp.array([new_vx_down, vy]),
                idx_below,
                new_timer,
                new_side,
                status,
            )

        def stay_on():
            new_x = x + vx

            # Wrap around screen
            new_x = jnp.where(new_x < 0, SCREEN_WIDTH - ew, new_x)
            new_x = jnp.where(new_x + ew > SCREEN_WIDTH, 0.0, new_x)

            return (
                jnp.array([new_x, y]),
                jnp.array([vx, vy]),
                p_idx,
                timer + 1,
                side,
                status,
            )

        def teleport_up_wrapper():
            return teleport_up(side, i, enemy_init_platform_idx, init_positions)

        def teleport_up(side, i, enemy_init_platform_idx, init_positions):
            target_top_idx = enemy_init_platform_idx[i]

            def pos_and_vel_for_top(idx):
                plat_x, plat_y, plat_w, plat_h = platforms[idx]
                left_side_x = plat_x + 5.0
                right_side_x = plat_x + plat_w - ew - 5.0

                orig_x = init_positions[i, 0]
                clamped_x = jnp.clip(orig_x, left_side_x, right_side_x)

                start_vx = jnp.where(side == 1, -0.5, 0.5)
                start_y = plat_y - eh
                return (clamped_x.astype(jnp.float32), start_y.astype(jnp.float32), start_vx.astype(jnp.float32))

            new_pos_x, new_pos_y, new_vx_up = pos_and_vel_for_top(target_top_idx)
            new_timer = jnp.array(0, dtype=jnp.int32)
            new_side = jnp.array(side, dtype=jnp.int32)

            return (
                jnp.array([new_pos_x, new_pos_y], dtype=jnp.float32),
                jnp.array([new_vx_up, 0.0], dtype=jnp.float32),  # vy = 0 on teleport up
                jnp.array(target_top_idx, dtype=jnp.int32),
                new_timer,
                new_side,
                status,
            )

        # Handle dead status first (so it overrides normal move)
        result = jax.lax.cond(
            status == 3,
            dead_logic,
            lambda: (
                jax.lax.cond(
                    (at_edge & has_platform_below) & (p_idx == 0),
                    lambda: jax.lax.cond(
                        timer >= TELEPORT_DELAY_FRAMES,
                        teleport_up_wrapper,
                        lambda: (jnp.array([-1000.0, -1000.0]), jnp.array([0.0, 0.0]), p_idx, timer + 1, side, status)
                    ),
                    lambda: jax.lax.cond(
                        at_edge & has_platform_below,
                        lambda: teleport_down(x + vx, vx, side, ew, eh, platforms, min_platform_below_y),
                        stay_on
                    ),
                )
            )
        )

        return result

    def conditional_step(pos, vel, idx, timer, side, i, active, status):
        return jax.lax.cond(
            active,
            lambda _: step_enemy(pos, vel, idx, timer, side, i, status),
            lambda _: (pos, vel, idx, timer, side, status),
            operand=None
        )

    indices = jnp.arange(enemy_pos.shape[0], dtype=jnp.int32)

    new_pos, new_vel, new_idx, new_timer, new_sides, new_status = jax.vmap(
        conditional_step,
        in_axes=(0, 0, 0, 0, 0, 0, 0, 0)
    )(enemy_pos, enemy_vel, enemy_platform_idx, enemy_timer, initial_sides, indices, active_mask, enemy_status)

    return new_pos, new_vel, new_idx, new_timer, new_sides, new_status




@jax.jit
def movement(state: PlayerState, game_state:GameState) -> PlayerState:    # Calculates movement of Player based on given state and action taken
    
    def normal(x):

        move = state.move * MOVE_SPEED
        jump_btn = state.jump
        vx = move
        # -------- phase / frame bookkeeping --------------------------
        start_jump = (jump_btn == 1) & state.on_ground & (state.jump_phase == 0)

        jump_phase = jnp.where(start_jump, 1, state.jump_phase)
        asc_left = jnp.where(start_jump, ASCEND_FRAMES, state.ascend_frames)

        # vertical speed for this frame
        vy = jnp.where(
            jump_phase == 1, ASCEND_VY,
            jnp.where(jump_phase == 2, DESCEND_VY,
                    jnp.where(state.on_ground, 0.0, DESCEND_VY))
        )

        # integrate position
        new_pos = state.pos + jnp.array([vx, vy])

        landed, bumped, y_land, y_bump, pow_bumped, bumped_idx = check_collision(new_pos, jnp.array([vx, vy]), PLATFORMS,
                                                                                POW_BLOCK, game_state.pow_block_counter)

        new_y = jnp.where(landed, y_land,
                        jnp.where(bumped, y_bump, new_pos[1]))

        # ---------- update phases after collision & time -------------
        # decrement ascend frames while ascending
        asc_left = jnp.where(jump_phase == 1, jnp.maximum(asc_left - 1, 0), asc_left)
        # switch to descend when ascent finished
        jump_phase = jnp.where((jump_phase == 1) & (asc_left == 0), 2, jump_phase)
        # head bump → descend immediately
        jump_phase = jnp.where(bumped & (vy < 0), 2, jump_phase)
        asc_left = jnp.where(bumped & (vy < 0), 0, asc_left)
        # landing → reset
        jump_phase = jnp.where(landed, 0, jump_phase)
        asc_left = jnp.where(landed, 0, asc_left)
        # walked off ledge → fall
        jump_phase = jnp.where((jump_phase == 0) & (~landed), 2, jump_phase)

        vy_final = jnp.where(
            jump_phase == 1, ASCEND_VY,
            jnp.where(jump_phase == 2, DESCEND_VY, 0.0)
        )

        new_x = jnp.clip(new_pos[0], 0, SCREEN_WIDTH - PLAYER_SIZE[0])

        return PlayerState(
            pos=jnp.array([new_x, new_y]),
            vel=jnp.array([vx, vy_final]),
            on_ground=landed,
            jump_phase=jump_phase.astype(jnp.int32),
            ascend_frames=asc_left.astype(jnp.int32),
            idx_right=state.idx_right,
            idx_left=state.idx_left,
            jump=state.jump,
            move=state.move,
            jumpL=state.jumpL,
            jumpR=state.jumpR,
            last_dir=state.last_dir,
            brake_frames_left=state.brake_frames_left,
            bumped_idx=bumped_idx,
            pow_bumped=pow_bumped,
            safe= state.safe
        )
    return lax.cond(state.safe, lambda x: x, normal , state)




@jax.jit
def player_step(state: PlayerState, action: chex.Array, game_state: GameState) -> PlayerState:
    def death_step(state: PlayerState) -> PlayerState:
        press_fire = (action == Action.FIRE) | (action == Action.LEFTFIRE) | (action == Action.RIGHTFIRE)
        press_right = (action == Action.RIGHT) | (action == Action.RIGHTFIRE)
        press_left = (action == Action.LEFT) | (action == Action.LEFTFIRE)

        cond = (press_fire | press_right | press_left) & jnp.all(state.pos == PLAYER_RESPWAN_XY)
        state = state._replace(safe=jnp.where(cond, False, state.safe))

        
        pos = state.pos
        pos = jnp.array([pos[0], pos[1] + DESCEND_VY], dtype=jnp.float32)
        new_pos = lax.cond(
            pos[1] > SCREEN_HEIGHT,
            lambda _: PLAYER_RESPWAN_XY,
            lambda _: pos,
            operand=None
        )
        state = state._replace(pos= new_pos)
        
        return state

    def step(state: PlayerState) -> PlayerState:
        # 1) decode buttons
        press_fire = (action == Action.FIRE) | (action == Action.LEFTFIRE) | (action == Action.RIGHTFIRE)
        press_right = (action == Action.RIGHT) | (action == Action.RIGHTFIRE)
        press_left = (action == Action.LEFT) | (action == Action.LEFTFIRE)

        state = state._replace(safe=jnp.where((press_fire | press_right | press_left), False, state.safe))
        # 2) reset horizontal/jump input on ground
        state0 = lax.cond(
            state.on_ground,
            lambda s: s._replace(move=0.0, jump=0, jumpL=False, jumpR=False),
            lambda s: s,
            state
        )


        # 3) only allow inputs when not jumping(player stutters when state.on_ground is used for check)
        press_fire = press_fire & (state0.jump == 0)
        press_right = press_right & (state0.jump == 0)
        press_left = press_left & (state0.jump == 0)
        

        # 3) set jump flag
        state1 = state0._replace(jump=jnp.where(press_fire, 1, state0.jump))

        # 4) walking/braking vs. jumping
        def walk_or_brake(s):
            # move right
            def mr(ss):
                return ss._replace(
                    move=movement_pattern[ss.idx_right],
                    idx_right=(ss.idx_right + 1) % pat_len,
                    idx_left=0,
                    last_dir=1,
                    brake_frames_left=0
                )

            # move left
            def ml(ss):
                return ss._replace(
                    move=-movement_pattern[ss.idx_left],
                    idx_left=(ss.idx_left + 1) % pat_len,
                    idx_right=0,
                    last_dir=-1,
                    brake_frames_left=0
                )

            # apply brake
            def br(ss):
                ss2 = ss._replace(
                    brake_frames_left=jnp.where((ss.last_dir != 0) & (ss.brake_frames_left == 0),
                                                BRAKE_DURATION, ss.brake_frames_left)
                )

                def do_brake(x):
                    nb = x.brake_frames_left - 1
                    return x._replace(
                        move=x.last_dir * BRAKE_SPEED,
                        brake_frames_left=nb,
                        last_dir=jnp.where(nb == 0, 0, x.last_dir)
                    )

                return lax.cond(ss2.brake_frames_left > 0, do_brake, lambda x: x._replace(move=0.0), ss2)

            return lax.cond(press_right, mr,
                            lambda ss: lax.cond(press_left, ml, br, ss), s)

        def jump_move(s):
            # mid-air jumping momentum
            def jr(ss):
                return ss._replace(
                    move=movement_pattern[ss.idx_right],
                    idx_right=(ss.idx_right + 1) % pat_len,
                    idx_left=0,
                    jumpR=True,
                    brake_frames_left=0,
                    last_dir=0
                )

            def jl(ss):
                return ss._replace(
                    move=-movement_pattern[ss.idx_left],
                    idx_left=(ss.idx_left + 1) % pat_len,
                    idx_right=0,
                    jumpL=True,
                    brake_frames_left=0,
                    last_dir=0
                )

            def js(ss):
                return ss._replace(
                    idx_left=0,
                    idx_right=0,
                    brake_frames_left=0,
                    last_dir=0
                )

            condR = press_right | s.jumpR
            condL = press_left | s.jumpL
            return lax.cond(condR, jr,
                            lambda ss: lax.cond(condL, jl, js, ss), s)

        state2 = lax.cond(state1.jump == 0, walk_or_brake, jump_move, state1)

        # 5) apply physics
        new_state = movement(state2, game_state)
        return new_state
    cond = state.safe & jnp.any(state.pos != PLAYER_RESPWAN_XY)
    return lax.cond(cond, death_step, step, state)
    


def draw_rect(image, x, y, w, h, color):
    y0 = jnp.clip(jnp.floor(y), 0, SCREEN_HEIGHT - 1).astype(jnp.int32)
    y1 = jnp.clip(jnp.floor(y + h), 0, SCREEN_HEIGHT).astype(jnp.int32)
    x0 = jnp.clip(jnp.floor(x), 0, SCREEN_WIDTH - 1).astype(jnp.int32)
    x1 = jnp.clip(jnp.floor(x + w), 0, SCREEN_WIDTH).astype(jnp.int32)

    mask_y = (jnp.arange(SCREEN_HEIGHT) >= y0) & (jnp.arange(SCREEN_HEIGHT) < y1)
    mask_x = (jnp.arange(SCREEN_WIDTH) >= x0) & (jnp.arange(SCREEN_WIDTH) < x1)
    mask = jnp.outer(mask_y, mask_x)

    color_arr = jnp.array(color, dtype=image.dtype).reshape(1, 1, 3)
    new_image = jnp.where(mask[:, :, None], color_arr, image)
    return new_image

def draw_digit(img, digit, x, y, size=6, color=(255, 255, 255)):
    seg = DIGIT_SEGMENTS[digit]

    def segment_rect(index):
        w, h = size, size // 2
        return [
            (x + 0, y + 0, w, h),                  # a
            (x + w, y + 0, h, w),                  # b
            (x + w, y + w, h, w),                  # c
            (x + 0, y + 2 * w, w, h),              # d
            (x - h, y + w, h, w),                  # e
            (x - h, y + 0, h, w),                  # f
            (x + 0, y + w, w, h),                  # g
        ][index]

    for i in range(7):
        img = lax.cond(
            seg[i] == 1,
            lambda im: draw_rect(im, *segment_rect(i), color),
            lambda im: im,
            img
        )
    return img

import jaxatari.rendering.jax_rendering_utils as ru
from jaxatari.renderers import JAXGameRenderer


class MarioBrosRenderer(JAXGameRenderer):
    def __init__(self):
        pass

    def render(self, state: MarioBrosState) -> jnp.ndarray:
        def white_screen(_: any) -> jnp.ndarray:
            return jnp.ones((SCREEN_HEIGHT, SCREEN_WIDTH, 3), dtype=jnp.uint8) * 255
        def continue_render(_: any) -> jnp.ndarray:
            image = jnp.zeros((SCREEN_HEIGHT, SCREEN_WIDTH, 3), dtype=jnp.uint8)

            # --- Draw player ---
            px, py = state.player.pos
            player_color = lax.cond(
                state.player.brake_frames_left > 0,
                lambda _: ENEMY_COLOR,
                lambda _: PLAYER_COLOR,
                operand=None
            )
            image = draw_rect(image, px, py, *PLAYER_SIZE, player_color)

            # --- Draw enemies ---
            def draw_enemy(i, img):
                should_draw = state.game.enemy.enemy_status[i] != 0

                def draw_fn(_):
                    ex, ey = state.game.enemy.enemy_pos[i]
                    return draw_rect(img, ex, ey, *ENEMY_SIZE, ENEMY_COLOR)

                def skip_fn(_):
                    return img

                return lax.cond(should_draw, draw_fn, skip_fn, operand=None)

            image = lax.fori_loop(0, state.game.enemy.enemy_pos.shape[0], draw_enemy, image)

            # --- Draw fireball ---
            fx, fy = state.game.fireball.pos
            image = draw_rect(image, fx, fy, *FIREBALL_SIZE, FIREBALL_COLOR)
            # --- Draw platforms ---
            def draw_platform(i, img):
                plat = PLATFORMS[i]
                color = lax.cond(i == 0, lambda _: GROUND_COLOR, lambda _: PLATFORM_COLOR, operand=None)
                return draw_rect(img, plat[0], plat[1], plat[2], plat[3], color)

            image = lax.fori_loop(0, PLATFORMS.shape[0], draw_platform, image)

            # --- Draw POW block only if hits < 3 ---
            def draw_pow(img):
                x, y, w, h = POW_BLOCK[0]
                return draw_rect(img, x, y, w, h, POW_COLOR)

            image = lax.cond(state.game.pow_block_counter > 0, draw_pow, lambda img: img, image)

            # --- Draw lives ---
            def draw_life(i, img):
                x = 5 + i * 12
                y = 5
                return draw_rect(img, x, y, 10, 10, (228, 111, 111))

            image = lax.fori_loop(0, state.lives, draw_life, image)

            # --- Draw score using 7-segment digits ---
            score = state.game.score
            digit_positions = 5  # Max digits to display

            def draw_score_digit(i, img):
                power = digit_positions - 1 - i
                divisor = 10 ** power
                digit = (score // divisor) % 10
                x = SCREEN_WIDTH - (digit_positions - i) * 12 - 5
                y = 5
                return draw_digit(img, digit, x, y)

            image = lax.fori_loop(0, digit_positions, draw_score_digit, image)

            return image
        
        return lax.cond(state.game.game_over, white_screen, continue_render, operand=None)



class JaxMarioBros(JaxEnvironment[
                       MarioBrosState, MarioBrosObservation, MarioBrosInfo, MarioBrosConstants]):  # copied and adapted from jax_kangaroo.py ln.1671
    # holds reset and main step function

    def __init__(self):
        self.action_set = [
            Action.NOOP,
            Action.FIRE,
            Action.UP,
            Action.RIGHT,
            Action.LEFT,
            Action.DOWN,
            Action.UPRIGHT,
            Action.UPLEFT,
            Action.DOWNRIGHT,
            Action.DOWNLEFT,
            Action.UPFIRE,
            Action.RIGHTFIRE,
            Action.LEFTFIRE,
            Action.DOWNFIRE,
            Action.UPRIGHTFIRE,
            Action.UPLEFTFIRE,
            Action.DOWNRIGHTFIRE,
            Action.DOWNLEFTFIRE
        ]

    def action_space(self) -> spaces.Discrete:
        return spaces.Discrete(len(self.action_set))

    def get_action_space(self) -> jnp.ndarray:
        return jnp.array(self.action_set)

    def _get_observation(self, state: MarioBrosState) -> MarioBrosObservation:
        return MarioBrosObservation(
            player_x=state.player.pos[0],
            player_y=state.player.pos[1]
        )

    def _get_info(self, state: MarioBrosState) -> MarioBrosInfo:
        return MarioBrosInfo(
            score=0
        )

    def reset(self, key=None) -> Tuple[MarioBrosObservation, MarioBrosState]:
        game = self.reset_game()
        obs = self._get_observation(game)
        return obs, game

    def reset_game(self) -> MarioBrosState:
        # Enemy 1 position (used also for enemy 3)
        new_state = MarioBrosState(
            player=PlayerState(
                pos=jnp.array([PLAYER_START_X, PLAYER_START_Y], dtype=jnp.float32),
                vel=jnp.array([0.0, 0.0]),
                on_ground=False,
                jump_phase=jnp.int32(0),
                ascend_frames=jnp.int32(0),
                idx_right=jnp.int32(0),
                idx_left=jnp.int32(0),
                jump=jnp.int32(0),
                move=jnp.array(0.0, dtype=jnp.float32),
                jumpL=False,
                jumpR=False,
                last_dir=jnp.int32(0),
                brake_frames_left=jnp.int32(0),
                bumped_idx=0,
                pow_bumped=False,
                safe= False
            ),
            game=GameState(
                enemy= new_Enemy(
                    enemy_pos=jnp.array([ENEMY_SPAWN[0], ENEMY_SPAWN[0], ENEMY_SPAWN[0], ENEMY_SPAWN[0]]),  # 3rd enemy = enemy 1 pos
                    enemy_move= jnp.array([ENEMY_MOVE, ENEMY_MOVE, ENEMY_MOVE, ENEMY_MOVE]),
                    enemy_platform_idx=jnp.array([-1, -1, -1, -1]),  # 3rd enemy same platform as enemy 1
                    enemy_status = jnp.array([0, 0, 0, 0]),
                    enemy_dir= jnp.array([0, 0, 0, 0]),
                    weak_frames= jnp.array([0, 0, 0, 0]),
                    enemy_spawn_idx= 0
                ),
                pow_block_counter=jnp.int32(3),
                score = jnp.int32(0),
                fireball=Fireball(
                    pos= jnp.array(FIREBALL_INIT_XY),
                    start_pat= FIREBALL_MOVEMENT_Start,
                    move_pat= FIREBALL_MOVEMENT,
                    count= FIREBALL_RESTART,
                    state= 0,
                    dir= -1
                ),
                game_over= False
        ),
            lives=jnp.int32(4)
        )

        return new_state

    def old_step(self, state: MarioBrosState, action: chex.Array) -> Tuple[
        MarioBrosObservation, MarioBrosState, float, bool, MarioBrosInfo]:
        # calls player_step function and check for collision with enemy
        def enemy_collision(s):
            obs, rS = self.reset()
            return obs, rS, 0.0, True, self._get_info(rS)

        def no_enemy_collision(s):
            return self._get_observation(state), MarioBrosState(
                player=s,
                game=state.game,
                lives=state.lives
            ), 0.0, True, self._get_info(state)

        return jax.lax.cond(check_enemy_collision(state.player.pos, state.game.enemy.enemy_pos), enemy_collision,
                            no_enemy_collision, player_step(state.player, action))

    from functools import partial
    @partial(jax.jit, static_argnums=0)
    def step(self, state: MarioBrosState, action: chex.Array) -> Tuple[
        MarioBrosObservation, MarioBrosState, float, bool, MarioBrosInfo]:
        """
        Performs one step of the environment given a state and an action.
        Handles player movement, enemy collisions, enemy patrol updates,
        POW hits, and enemy status changes.

        Args:
            state: Current MarioBrosState.
            action: Action chosen by the agent.

        Returns:
            obs: MarioBrosObservation (player position).
            new_state: Updated MarioBrosState.
            reward: Float reward (0.0 here).
            done: Boolean flag if episode ended.
            info: MarioBrosInfo.
        """

        def game_over(_) -> MarioBrosState:
            jax.debug.print("Game Over")
            def restart():
                obs, state = self.reset()
                info = self._get_info(state)
                return obs, state, 0.0, False, info
            def wait():
                obs = self._get_observation(state)
                info = self._get_info(state)
                return obs, state, 0.0, False, info
            cond = (action == Action.FIRE) | (action == Action.LEFTFIRE) | (action == Action.RIGHTFIRE) | (action == Action.RIGHT) | (action == Action.RIGHTFIRE) | (action == Action.LEFT) | (action == Action.LEFTFIRE)
            return lax.cond(cond, restart, wait)

        def step(_) -> MarioBrosState:
            # 1) Advance player state given action
            new_player = player_step(state.player, action, state.game)
            bumped_idx = new_player.bumped_idx
            pow_bumped = new_player.pow_bumped
            # 2) Detect collisions between player and enemies
            def check_enemy_collision_per_enemy(player_pos, enemy_positions):
                px, py = player_pos
                pw, ph = PLAYER_SIZE
                ex, ey = enemy_positions[:, 0], enemy_positions[:, 1]
                ew, eh = ENEMY_SIZE

                overlap_x = (px < ex + ew) & (px + pw > ex)
                overlap_y = (py < ey + eh) & (py + ph > ey)
                return overlap_x & overlap_y  # bool array per enemy

            def check_fireball_collision(fb: Fireball, player_pos) -> bool:
                px, py = player_pos
                pw, ph = PLAYER_SIZE
                fx, fy = fb.pos
                fw, fh = FIREBALL_SIZE
                
                overlap_x = (px < fx + fw) & (px + pw > fx)
                overlap_y = (py < fy + fh) & (py + ph > fy)
                
                return overlap_x & overlap_y

            collided_mask = check_enemy_collision_per_enemy(new_player.pos, state.game.enemy.enemy_pos)

            fireball_hit = check_fireball_collision(state.game.fireball, new_player.pos)
            # Check if any collided enemy is strong (status==2)
            strong_enemy_hit = jnp.any(collided_mask & (state.game.enemy.enemy_status == 1))

            # --- Handling collision with strong enemy: reset Mario position only ---
            def on_hit(_):
                # Spielerleben um 1 reduzieren
                new_lives = state.lives - 1
                
                # Mario an Respawn-Position setzen, falls Leben >= 0
                new_player_updated = new_player._replace(safe= True, jump_phase= jnp.int32(0))
                
                # Neuer Zustand mit reduziertem Leben und neuer Position
                new_game = state.game._replace(game_over=jnp.where(state.lives > 0, False, True))

                new_state = MarioBrosState(
                    player=new_player_updated,
                    game=new_game,
                    lives=new_lives
                )
                
                
                # Beobachtung und Info aus dem endgültigen Zustand holen
                obs = self._get_observation(new_state)
                info = self._get_info(new_state)
                
                return obs, new_state, 0.0, False, info

            # --- No strong enemy collision: normal game progress ---
            def on_no_hit(_):
                # 3) Update enemy patrol movements
                # Move this before enemy_step:

                # Then call enemy_step:
                new_enemy = enemy_step(state.game.enemy)
                new_enemy = check_enemy_spawn(new_enemy)
                jax.debug.print("Enemy = {}", new_enemy)
                new_fireball = fireball_step(state.game.fireball)
                # 4) Update enemy spawn delay timer (caps at ENEMY_SPAWN_FRAMES)

                # 5) POW hit logic and platform bump detection
                # only count hits if we haven't hit it 3 times yet
                pow_hit = pow_bumped & (state.game.pow_block_counter > 0)
                new_pow_block_counter = jnp.maximum(state.game.pow_block_counter - pow_hit, 0)
                bumped_idx_final = jnp.where(pow_hit, -2, bumped_idx)

                # 6) Toggle enemy status for bumped platforms/POW (strong <-> weak)
                def set_weak(enemy: new_Enemy):
                    value = enemy.enemy_status.dtype.type(2)
                    return enemy._replace(enemy_status=enemy.enemy_status.at[:].set(value))

                # Use toggle_mask to decide where to toggle status
                new_enemy = jax.lax.cond(
                    bumped_idx_final >= 0,
                    set_weak,
                    lambda x: x,
                    new_enemy  # UPDATED status used here
                )

    
                def kill(enemy: new_Enemy, collided_mask: jnp.ndarray) -> Tuple[new_Enemy, jnp.ndarray]:
                    weak_hits = (enemy.enemy_status == 2) & collided_mask

                    # Neue Status: getroffene schwache Gegner sterben -> Status 0
                    new_status = jnp.where(weak_hits, 0, enemy.enemy_status)

                    # Position getöteter Gegner auf [0, 0] setzen
                    new_pos = jnp.where(weak_hits[:, None], jnp.array([0, 0]), enemy.enemy_pos)

                    # Score zählen
                    num_killed = jnp.sum(weak_hits.astype(jnp.int32))

                    # Rückgabe des neuen Zustands
                    return enemy._replace(enemy_status=new_status, enemy_pos=new_pos), num_killed

                # --- Collision logic with enemies after enemy step ---

                # Re-check collisions with updated enemy positions
                collided_mask = check_enemy_collision_per_enemy(new_player.pos, new_enemy.enemy_pos)
                # Mark weak enemies (status==1) hit by player as dead (status=3)
                new_enemy, scored = kill(new_enemy, collided_mask)


                # Count and score
                score_gain = scored * 800
                new_score = state.game.score + score_gain

                
                # 7) Construct updated game state with new enemy info and POW hits
                new_game = GameState(
                    enemy=new_enemy,
                    pow_block_counter=new_pow_block_counter,
                    score = new_score,
                    fireball = new_fireball,
                    game_over= state.game.game_over
                )

                # 8) Final updated full state with player and game info
                new_state = MarioBrosState(
                    player=new_player,
                    game=new_game,
                    lives=state.lives
                )

                # 9) Observation is just player position
                obs = self._get_observation(new_state)

                return obs, new_state, 0.0, False, self._get_info(new_state)

            # 10) Return based on whether strong enemy was hit
            cond = (strong_enemy_hit | fireball_hit) & jnp.logical_not(new_player.safe)
            return jax.lax.cond(cond, on_hit, on_no_hit, new_player)
        return lax.cond(state.game.game_over, game_over, step, state)


# run game with: python scripts\play.py --game mariobros
if __name__ == "__main__":
    pygame.init()
    screen = pygame.display.set_mode(
        (SCREEN_WIDTH * WINDOW_SCALE, SCREEN_HEIGHT * WINDOW_SCALE)
    )
    pygame.display.set_caption("JAX Mario Bros Prototype")
    clock = pygame.time.Clock()
    game = JaxMarioBros()
    renderer = MarioBrosRenderer()

    _, state = game.reset()
    running = True

    while running:
        #    _,state,_,_,_ = game.step(state, )
        renderer.render(state)

        clock.tick(60)

    pygame.quit()