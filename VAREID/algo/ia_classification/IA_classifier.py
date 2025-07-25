import argparse
import ast
import os
import shutil
import warnings
import json

import numpy as np
import pandas as pd
import torch
import yaml
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms
from torchvision.transforms import functional as F
from torchvision.models import resnet50
from torchvision.ops import nms

from VAREID.libraries.io.format_funcs import load_config, load_json, save_json, split_dataframe, join_dataframe
from VAREID.libraries.utils import path_from_file


def xywh_to_xyxy(bbox: list):
    x, y, w, h = bbox
    x1 = x
    y1 = y
    x2 = x + w
    y2 = y + h
    return [x1, y1, x2, y2]


class CustomImageDataset(Dataset):
    def __init__(self, dataframe, transform=None):
        self.img_data = dataframe
        self.transform = transform

    def __len__(self):
        return len(self.img_data)

    def __getitem__(self, idx):

        # Read image as PIL Image
        image = Image.open(self.img_data.iloc[idx]["image_path"]).convert("RGB")

        # Get the bounding box coordinates
        bbox = xywh_to_xyxy(self.img_data.iloc[idx]["bbox"])

        # Crop the image according to bbox
        image = image.crop((int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])))

        # Flip the image if left viewpoint
        if "left" in self.img_data.iloc[idx]["viewpoint"]:
            image = F.hflip(image)

        if self.transform:
            image = self.transform(image)

        return image


class BinaryClassResNet50(nn.Module):
    def __init__(self):
        super(BinaryClassResNet50, self).__init__()
        self.resnet50 = resnet50(pretrained=True)
        for param in self.resnet50.parameters():
            param.requires_grad = False  # Freeze parameters of pre-trained model
        num_ftrs = self.resnet50.fc.in_features
        self.resnet50.fc = nn.Linear(num_ftrs, 2)  # We have two classes either 0 or 1

    def forward(self, x):
        x = self.resnet50(x)
        return x


def load_model(model_path, device):
    model = BinaryClassResNet50()
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.to(device)
    model.eval()
    return model


def filter_dataframe(df, config):
    # Filter based on accepted viewpoint
    viewpoint_condition = df["viewpoint"].isin(config["viewpoints"])
    # Special condition - only applies to gt annotated data where the species was correctly identified
    if "annot species" in df.keys():
        species_condition = df["annot_species"] == config["species"]
    else:
        species_condition = True

    # Create a mask for rows to be filtered out
    filter_mask = ~(species_condition & viewpoint_condition)

    # Split the dataframe
    filtered_out = df[filter_mask].copy().reset_index(drop=True)
    filtered_test = df[~filter_mask].copy().reset_index(drop=True)

    # Add NaN columns to filtered_out
    filtered_out["softmax_output_0"] = np.nan
    filtered_out["softmax_output_1"] = np.nan

    return filtered_test, filtered_out


def test_new(dataloader, model, device):
    model.eval()
    all_softmax_outputs = []

    with torch.no_grad():
        for X in dataloader:  #
            X = X.to(device)
            pred = model(X)
            pred_softmax = torch.softmax(pred, dim=1)
            all_softmax_outputs.extend(pred_softmax.cpu().numpy())

    all_softmax_outputs = np.array(all_softmax_outputs)
    return all_softmax_outputs


def apply_nms(df, iou_threshold):
    df = df.sort_values("softmax_output_1", ascending=False)
    boxes = np.array([xywh_to_xyxy(bbox) for bbox in df["bbox"]])
    scores = df["softmax_output_1"].values
    boxes = torch.as_tensor(boxes).float()
    scores = torch.as_tensor(scores).float()
    keep = nms(boxes, scores, iou_threshold)
    return df.iloc[keep]


def expand_bbox_columns(df):
    # Extract bbox components into separate columns
    bbox_data = df["bbox"].apply(
        lambda x: pd.Series(x, index=["bbox x", "bbox y", "bbox w", "bbox h"])
    )

    # Add the new columns to the dataframe
    df = pd.concat([df, bbox_data], axis=1)
    return df


def main(args):
    print("Loading configuration...")
    config = load_config(path_from_file(__file__, "IA_classifier_config.yaml"))

    print("Setting up device...")
    device = torch.device(config["device"] if torch.cuda.is_available() else "cpu")

    print("Loading and preprocessing data...")
    data = load_json(args.in_json_path)
    df = join_dataframe(data)

    # Expand bbox column into separate x, y, w, h columns
    df = expand_bbox_columns(df)

    print(f"The length of input JSON is: {len(df)}")
    filtered_test, filtered_out = filter_dataframe(df, config)

    print("Setting up transformations and data loader...")
    transform = transforms.Compose(
        [
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )
    dataset = CustomImageDataset(filtered_test, transform)
    dataloader = DataLoader(dataset, batch_size=config["batch_size"], shuffle=False)

    print("Loading model...")
    with warnings.catch_warnings():  # Add this line
        warnings.filterwarnings("ignore", category=UserWarning)
        model = load_model(args.model_checkpoint_path, device)

    print("Starting testing...")
    all_softmax_outputs = test_new(dataloader, model, device)  #

    print(
        "Testing completed. Appending softmax outputs to JSON and starting post-processing..."
    )
    filtered_test["softmax_output_0"] = all_softmax_outputs[:, 0]
    filtered_test["softmax_output_1"] = all_softmax_outputs[:, 1]

    # Step 1: Filter based on threshold_CA
    above_threshold = filtered_test[
        filtered_test["softmax_output_1"] > config["threshold_CA"]
    ].reset_index(drop=True)
    below_threshold = filtered_test[
        filtered_test["softmax_output_1"] <= config["threshold_CA"]
    ].reset_index(drop=True)

    print(f"The length of softmax thresholded JSON is: {len(above_threshold)}")

    # Step 2: Filter based on log(aspect_ratio)
    above_threshold["log_AR"] = np.log(
        above_threshold["bbox w"] / above_threshold["bbox h"]
    )
    ar_filtered = above_threshold[
        (above_threshold["log_AR"] >= config["min_log_AR"])
        & (above_threshold["log_AR"] <= config["max_log_AR"])
    ].reset_index(drop=True)
    ar_filtered_out = above_threshold[
        (above_threshold["log_AR"] < config["min_log_AR"])
        | (above_threshold["log_AR"] > config["max_log_AR"])
    ].reset_index(drop=True)

    print(f"The length of AR thresholded JSON is: {len(ar_filtered)}")

    # Step 3: Apply NMS
    grouped = ar_filtered.groupby("image_path")
    all_results = []
    nms_filtered_out = []
    for name, group in grouped:
        result_df = apply_nms(group, config["NMS_threshold"])
        all_results.append(result_df)
        # Keep track of removed annotations
        removed = group[~group.index.isin(result_df.index)]
        nms_filtered_out.append(removed)

    if all_results:
        nms_filtered = pd.concat(all_results).reset_index(drop=True)
        print(f"The length of NMS thresholded JSON is: {len(nms_filtered)}")
    else:
        print("Warning: No objects passed NMS filtering")
        nms_filtered = pd.DataFrame(
            columns=ar_filtered.columns
        )  # Create empty DataFrame with same columns

    if nms_filtered_out:
        nms_filtered_out = pd.concat(nms_filtered_out).reset_index(drop=True)
    else:
        nms_filtered_out = pd.DataFrame(columns=ar_filtered.columns)

    # print(nms_filtered_out)
    print(f"The length of NMS thresholded JSON is: {len(nms_filtered)}")

    # Add annotations_census column
    nms_filtered["annotations_census"] = True
    below_threshold["annotations_census"] = False
    ar_filtered_out["annotations_census"] = False
    nms_filtered_out["annotations_census"] = False
    filtered_out["annotations_census"] = False

    # Concatenate all dataframes
    final_df = pd.concat(
        [
            nms_filtered,
            below_threshold,
            ar_filtered_out,
            nms_filtered_out,
            filtered_out,
        ],
        ignore_index=True,
    )

    # Rename to CA_score (desired output)
    final_df = final_df.rename(columns={"softmax_output_1": "CA_score"})

    print(f"The length of final concatenated JSON is: {len(final_df)}\n")

    # Save the updated DataFrame to a new json file
    cac_dir = os.path.dirname(args.out_json_path)
    if os.path.exists(cac_dir):
        print("Removing Previous Instance of Experiment...")
        shutil.rmtree(cac_dir)

    print("Saving the results...")
    os.makedirs(cac_dir, exist_ok=True)
    annotations = split_dataframe(final_df)
    save_json(annotations,args.out_json_path)

    print(
        f"JSON with softmax outputs and census annotations saved to: {args.out_json_path}"
    )
    print("All tasks completed successfully!")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Run IA classifier to determine identifiability of animal annotations"
    )
    parser.add_argument(
        "in_json_path",
        type=str,
        help="The full path to the viewpoint classifier output json to use as input",
    )
    parser.add_argument(
        "model_checkpoint_path", type=str, help="The full path to the model checkpoint"
    )
    parser.add_argument(
        "out_json_path", type=str, help="The full path to the output json file"
    )
    args = parser.parse_args()

    main(args)
