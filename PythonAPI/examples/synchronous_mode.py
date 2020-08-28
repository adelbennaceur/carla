#!/usr/bin/env python

# Copyright (c) 2019 Computer Vision Center (CVC) at the Universitat Autonoma de
# Barcelona (UAB).
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

import glob
import os
import sys

try:
    sys.path.append(
        glob.glob(
            "../PythonApi/carla/dist/carla-*%d.%d-%s.egg"
            % (
                sys.version_info.major,
                sys.version_info.minor,
                "win-amd64" if os.name == "nt" else "linux-x86_64",
            )
        )[0]
    )
except IndexError:
    pass

import carla

import random

try:
    import pygame
except ImportError:
    raise RuntimeError("cannot import pygame, make sure pygame package is installed")

try:
    import numpy as np
except ImportError:
    raise RuntimeError("cannot import numpy, make sure numpy package is installed")

try:
    import queue
except ImportError:
    import Queue as queue


class CarlaSyncMode(object):
    """
    Context manager to synchronize output from different sensors. Synchronous
    mode is enabled as long as we are inside this context

        with CarlaSyncMode(world, sensors) as sync_mode:
            while True:
                data = sync_mode.tick(timeout=1.0)

    """

    def __init__(self, world, *sensors, **kwargs):
        self.world = world
        self.sensors = sensors
        self.frame = None
        self.delta_seconds = 1.0 / kwargs.get("fps", 20)
        self._queues = []
        self._settings = None

    def __enter__(self):
        self._settings = self.world.get_settings()
        self.frame = self.world.apply_settings(
            carla.WorldSettings(
                no_rendering_mode=False,
                synchronous_mode=True,
                fixed_delta_seconds=self.delta_seconds,
            )
        )

        def make_queue(register_event):
            q = queue.Queue()
            register_event(q.put)
            self._queues.append(q)

        make_queue(self.world.on_tick)
        for sensor in self.sensors:
            make_queue(sensor.listen)
        return self

    def tick(self, timeout):
        self.frame = self.world.tick()
        data = [self._retrieve_data(q, timeout) for q in self._queues]
        assert all(x.frame == self.frame for x in data)
        return data

    def __exit__(self, *args, **kwargs):
        self.world.apply_settings(self._settings)

    def _retrieve_data(self, sensor_queue, timeout):
        while True:
            data = sensor_queue.get(timeout=timeout)
            if data.frame == self.frame:
                return data


def draw_image(surface, image, blend=False):
    array = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
    array = np.reshape(array, (image.height, image.width, 4))
    array = array[:, :, :3]
    array = array[:, :, ::-1]
    image_surface = pygame.surfarray.make_surface(array.swapaxes(0, 1))
    if blend:
        image_surface.set_alpha(100)
    surface.blit(image_surface, (0, 0))


def get_font():
    fonts = [x for x in pygame.font.get_fonts()]
    default_font = "ubuntumono"
    font = default_font if default_font in fonts else fonts[0]
    font = pygame.font.match_font(font)
    return pygame.font.Font(font, 14)


def should_quit():
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return True
        elif event.type == pygame.KEYUP:
            if event.key == pygame.K_ESCAPE:
                return True
    return False


def main():
    width = 640
    height = 480
    actor_list = []
    pygame.init()

    display = pygame.display.set_mode(
        (width, height), pygame.HWSURFACE | pygame.DOUBLEBUF
    )
    font = get_font()
    clock = pygame.time.Clock()

    client = carla.Client("localhost", 2000)
    client.set_timeout(2.0)
    world = client.get_world()

    try:
        m = world.get_map()
        rec_pos = random.choice(m.get_spawn_points())
        waypoint = m.get_waypoint(rec_pos.location)

        blueprint_library = world.get_blueprint_library()

        # blueprints
        vehicle_bp = blueprint_library.filter("model3")[0]
        rgb_camera_bp = blueprint_library.find("sensor.camera.rgb")
        seg_camera_bp = blueprint_library.find("sensor.camera.semantic_segmentation")
        # Transforms

        cam_transform = carla.Transform(
            carla.Location(x=-5.5, z=2.8), carla.Rotation(pitch=-15),
        )
        cam_transform2 = carla.Transform(carla.Location(x=1.6, z=1.7))

        car_spawn_loc = carla.Transform(
            carla.Location(x=-6.446170, y=-79.055023, z=0.275307),
            carla.Rotation(pitch=0.000000, yaw=92.004189, roll=0.000000),
        )

        # spawn actors
        vehicle = world.spawn_actor(vehicle_bp, rec_pos)
        vehicle.set_simulate_physics(False)

        camera_rgb = world.spawn_actor(rgb_camera_bp, cam_transform2, attach_to=vehicle)
        camera_semseg = world.spawn_actor(
            seg_camera_bp, cam_transform2, attach_to=vehicle
        )

        actor_list.append(vehicle)
        actor_list.append(camera_rgb)
        actor_list.append(camera_semseg)

        # Create a synchronous mode context.
        with CarlaSyncMode(world, camera_rgb, camera_semseg, fps=30) as sync_mode:
            while True:
                if should_quit():
                    return
                clock.tick()

                # Advance the simulation and wait for the data.
                snapshot, image_rgb, image_semseg = sync_mode.tick(timeout=2.0)

                # Choose the next waypoint and update the car location.
                waypoint = random.choice(waypoint.next(1.5))
                vehicle.set_transform(waypoint.transform)

                # save images
                image_rgb.save_to_disk("rgb_images/%08d" % image_rgb.frame)
                image_semseg.save_to_disk("seg_images/%08d" % image_semseg.frame)

                image_semseg.convert(carla.ColorConverter.CityScapesPalette)
                image_semseg.save_to_disk("seg_images_city/%08d" % image_semseg.frame)

                fps = round(1.0 / snapshot.timestamp.delta_seconds)

                # Draw the display.
                draw_image(display, image_rgb)
                draw_image(display, image_semseg, blend=True)
                display.blit(
                    font.render(
                        "% 5d FPS (real)" % clock.get_fps(), True, (255, 255, 255)
                    ),
                    (8, 10),
                )
                display.blit(
                    font.render("% 5d FPS (simulated)" % fps, True, (255, 255, 255)),
                    (8, 28),
                )
                pygame.display.flip()

    finally:

        print("destroying actors.")
        for actor in actor_list:
            actor.destroy()

        pygame.quit()
        print("done.")


if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:
        print("\nCancelled by user. Bye!")
