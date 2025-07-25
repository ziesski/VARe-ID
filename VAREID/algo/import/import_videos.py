import argparse
import yaml

from VAREID.libraries.io.con_funcs import import_video_folder
from VAREID.libraries.utils import path_from_file

def load_config(config_file_path):
    with open(config_file_path, "r") as file:
        config_file = yaml.safe_load(file)
    return config_file


if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Import directory of VAREID videos')
    parser.add_argument('dir_in', type=str, help='The directory to import')
    parser.add_argument('out_path', type=str, help='The full path to the .json file to store video data in')
    args = parser.parse_args()
    print('Importing videos')

    config = load_config(path_from_file(__file__, "../detection/detector_config.yaml"))

    sep_idx = args.out_path.rfind("/")
    dir_out = args.out_path[:sep_idx]
    out_file = args.out_path[sep_idx:].replace("/","")
    # Import images to database
    image_table = import_video_folder(args.dir_in, dir_out, out_file, config["video_fps"], config["video_max_frames"])
