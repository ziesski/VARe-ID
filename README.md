# Video-Based Animal Re-Identification (VARe-ID) from Multiview Spatio-Temporal Track Clustering

This work presents a modular software pipeline and end-to-end workflow for video-based animal re-identification, which assigns consistent individual IDs by clustering multiview spatio-temporal tracks with minimal human intervention. Starting from raw video, the system detects and tracks animals, scores and selects informative left/right views, computes embeddings, clusters annotations by viewpoint, and then links clusters across time and varying perspectives using spatio-temporal continuity. Automated consistency checks resolve remaining ambiguities. Preliminary experiments demonstrate near-perfect identification accuracy with very limited manual verification. The workflow is designed to be generalizable across species. Currently, trained models support Grevy’s and Plains zebras, with plans to expand to a broader range of species.

<p align="center">
  <img src="https://github.com/user-attachments/assets/8ddd01a3-6511-40f7-b182-41c479ad447b" alt="image" width="862" height="896">
</p>




### Tags: 
- Software
- Animal-Ecology

---
# Explanation

## Definitions of Key Terms and Concepts

* **Animal Re-Identification (re-id)**: The process of determining if an animal has been seen before by matching it against a database of images with known identity labels. The paper addresses this problem in the context of long video sequences.
* **Multiview Spatio-Temporal Track Clustering**: A novel framework introduced for animal re-identification. It works by clustering tracked animal detections from different viewpoints (multiview) and across time (spatio-temporal) to correctly identify individuals.
* **Identifiable Annotation (IA)**: An annotation, or detected animal image, that contains sufficient distinguishing information for reliable individual identification. For Grévy's zebras, an IA must show both the hip and chevron patterns on either the left or right side.
* **Human-in-the-loop**: The involvement of human decisions to confirm animal identities when the automated system is uncertain or to correct algorithmic errors.

## Repository File Structure

The following is a simplified hierarchy of the file structure using in this repository.

```
VAREID
├── algo
|   ├── detection/
|   ├── frame_sampling/
|   ├── ia_classification/
|   ├── import/
|   ├── lca/
|   ├── miew_id/
|   ├── postprocessing/
|   ├── species_identification/
|   └── viewpoint_classification/
├── drivers/
├── models/
├── tools/
├── libraries
|   ├── db/
|   ├── io/
|   ├── logging/
|   ├── ui/
|   └── constants.py
├──   config.yaml
├──   environment.yaml
└──   snakefile.smk
```

The repository can generally be split into four groups of code:

#### Algorithm Components
Algorithm components are the invidual steps of the pipeline, such as detection or species classification. They are contained in `VAREID/algo/[component_name]/` in separate directories. In some specific cases, two components may share the same directory, such as `video_detector.py` and `image_detector.py`. Their only dependency (within this repository) would be library functions. Every component here should have an executable script to run that step of the pipeline.

For more information on each algorithm component, please view the **README** files in each of their corresponding directories. Their arguments are also documented via the **argparse** python library.

#### Pipeline (Snakemake)
The pipeline's workflow is built using **Snakemake**. The workflow is defined in `snakefile.smk`, via executions to **driver scripts**.

The snakefile reads in a configfile structured like `config.yaml`. To build a configfile, please follow the notations found in the example `config.yaml`.

#### Driver Scripts
Driver scripts serve as connectors between the pipeline and the algorithm components. They handle determining conditional arguments passed to algorithm components (such as flags or variations in parameters based on image vs. video mode), setting up logging, building the command, and executing the algorithm component. Every algorithm component must have a driver script associated with it.

#### Libraries
The libraries contain all util functions used throughout the pipeline. These libraries range from database operations (e.g. image tables and directories), IO (image/video importing, loading/saving data, etc.), logging, UI, and more.

### Other important files and directories...
In addition to the above structure, there's a few more important directories to note. 

#### Models
All models are stored in the `VAREID/models/` directory. This primarily includes the `.pth` files for the viewpoint classifier and IA classifier models. It also includes the verifiers probabilities used by LCA, but this is being phased out.

#### Tools
This directory contains some prototype tools that provide convenience and extra functionality to users. `visualize.py` is a script that draws and labels specific annotations. `extrapolate_ggr_gps.py` extrapolates GPS data for images missing it, which is specific functionality for images taken by the same camera with timestamp data.

#### environment.yaml
This is the file defining the python environemnt requirements for this repository. Use this file with a package manager like **conda** to build an environment. More on this in the **How-To** section.

## Pipeline Workflow

The following is a flowchart describing the workflow of the pipeline, along with the associated driver script for each stage.

```mermaid
flowchart LR
 subgraph s1["data_video == False"]
        n15["<b>Miew-Id</b><br>mid_driver.py"]
        n20["<b>LCA</b><br>lca_driver.py"]
  end
 subgraph s2["data_video == True"]
        n16["<b>Frame Sampling</b><br>fs_driver.py"]
        n17["<b>Miew-Id</b><br>mid_driver.py"]
        n18["<b>LCA</b><br>lca_driver.py"]
  end
 subgraph s3["data_video == False"]
        n21@{ label: "<b><span style=\"--tw-scale-x:\">Image Importer</span><br style=\"--tw-scale-x:\"></b>import_image_detector.py" }
        n25@{ label: "<span style=\"--tw-scale-x:\"><b>Image Detector<br style=\"--tw-scale-x:\"></b></span>dt_image_driver.py" }
  end
 subgraph s4["data_video == True"]
        n22["<b>Video Importer<br></b>import_video_detector.py"]
        n24["<b>Video Detector<br></b>dt_video_driver.py"]
  end
    n6["<b>Species Classifier</b><br>si_driver.py"] --> n7["<b>Viewpoint Classifier<br></b>vc_driver.py"]
    n7 --> n8["<b>IA Classifier<br></b>iac_driver.py"]
    n8 --> n10["<b>IA Filterer</b><br>iaf_driver.py"]
    n10 --> n16 & n15
    n16 --> n17
    n16 -- annotations --> n18
    n17 -- embeddings --> n18
    n15 -- embeddings --> n20
    n10 -- annotations --> n20
    n22 --> n24
    n21 --> n25
    n24 --> n6
    n25 --> n6
    n18 --> n26["<b>Postprocessing</b><br>post_driver.py"]

    n16@{ shape: rect}
    n21@{ shape: rect}
    n25@{ shape: rect}
    n7@{ shape: rect}
    n8@{ shape: rect}
    n26@{ shape: rect}
    style n26 stroke-width:4px,stroke-dasharray: 5
```

One important detail to note immediately is that postprocessing is external from the pipeline's workflow! This section, as will be explained below, requires human interaction and thus is not automatically ran by the pipeline. It is run separately and for video data only.

### Input Format
The pipeline's input is any recursive directory structure. For image mode, the pipeline will read in ALL images within the provided directory and its child directories. For video mode, we will read all videos. **When running the pipeline on videos, each video must have a matching (same file name) SRT file located in the same directory.** In other words, the absolute paths to the video and SRT file only differ by their file extension. Each entry of the SRT file should be formatted like the following:

```
1
00:00:00,000 --> 00:00:00,033
<font size="36">SrtCnt : 1, DiffTime : 33ms
2023-01-19 11:48:31,795,565
[iso : 100] [shutter : 1/2000.0] [fnum : 280] [ev : 0] [ct : 4823] [color_md : default] [focal_len : 224] [latitude: 0.386694] [longitude: 36.893198] [altitude: 23.900000] 
</font>
```

### Pipeline Stages & Algorithms

#### 1. Import
Importing's main goal is generating the `image_data.json` or `video_data.json` file describing each image (or frame for videos) in terms of metadata, including the absolute path to the image. For videos, this also includes splitting and saving the video into frames as well as parsing an SRT file to assign timestamps to frames.

#### 2. Detection
Detection uses YOLO to create detections for all images in the json files from above. Video detection also generates tracking IDs for each detection. The detections are saved as annotations.

#### 3. Species Classification
The species of each annotation is generated via Bioclip. For now, this includes Grevys Zebras, Plains Zebras, or neither.

#### 4. Viewpoint Classification
The viewpoint of each annotation is generated. The viewpoint is a combination of the following classifiers: `[up, front, back, left, right]`.

#### 5. Identifiable Annotation (IA) Classification
Each annotation is assessed for its quality and ability to be identified. They are assigned a score and assigned a boolean for whether they are identifiable or not based on a threshold.

#### 6. Identifiable Annotation (IA) Filtering
This step filters out all annotations that were marked as not identifiable and simplifies the viewpoint to `left` or `right`.

#### 7. *Frame Sampling*
This is a *video only* process. This step further filters annotations by performing non-maximum supression over sets of consecutive tracking ids, maximizing the score from IA classification.

#### 8. Miew-Id
This step generates embeddings for all remaining annotations.

#### 9. Local Clusters and Alternatives (LCA) Algorithm
This step clusters the annotations by their embeddings and assigns cluster ids.

#### 10. *Post-processing and ID Assignment*
Applies final consistency checks, resolves cluster overlaps, handles manual verification when needed, assigns final unique IDs, and integrates non-identifiable annotations via tracking links.

---
# How-To Guides

This section walks through how to use this repository and its features. It is split into sections based on the types of tasks you're looking to accomplish.

## Setting up a Python Environment
You'll need to setup a python envionment that meets the requirements layed out in `environment.yaml`. There's several package managers that revolve around **conda** as well as the more-efficient reimplementation **mamba**. Pick your favorite and use its documentation to set up an environment. To setup the environment, you'll need to do a command similar to the following:

From the parent directory...

| Package Manager | Command |
| ----------- | ----------- |
| conda (Miniconda/Anaconda) | `conda env create -n [env name] -f environment.yaml` |
| mamba | `mamba create -n [env name] -f environment.yaml` |

The choice of what package manager to use is up to you.

### Activate your environment:
The commands to activate your envioronment are as follows:

| Package Manager | Command |
| ----------- | ----------- |
| conda (Miniconda/Anaconda) | `conda activate [env name]` |
| mamba | `mamba activate [env name]` |

## Setting up a Configfile
Please follow the instructions provided by comments in `config.yaml`. You can directly edit and use this file if you wish, but we **highly recommend** filling out a copy. This way, you can save the configs for each experiment and refer to them later (or run several experiments at once). 

Unless you'd like to customize the exact output filenames and directories, the following config fields matter the most:

The following fields are **required**:
- `data_dir_out`: This is the output directory to save to.
- `data_dir_in`: This is the input directory to read to.
- `data_video`: This is a boolean (True/False) specifying whether to process image or video data.

The following fields are **optional** and either have default (recommended) values already in the configfile or are blank (fully optional):
- `dt_gt_file` and `dt_filtered_out_file`: In the case that you're running image data with ground truth data, you can find and filter detections by IOU (Intersection over Union) with the ground truth detections.
- `fs_stage1_out_file`: This field, if supplied, will save an additional output from frame sampling after its first stage.
- `lca_separate_viewpoints`: This field specifies whether to split and save annotation files by each viewpoint or to save them alltogether. **In video mode, this MUST be True!**

## Running the Pipeline
To run the pipeline, you'll execute `snakefile.smk`. Remember: the pipeline does NOT run postprocessing. This is run separately.

**Please run the snakefile from the parent directory in this repository.** For more information on how to run a snakefile (e.g. available flags), please view the [Snakemake Docs](https://snakemake.readthedocs.io/en/stable/executing/cli.html). The most important flags you'll need to specify are as follows: 

| Flag | Function |
| ----------- | ----------- |
| -s | The path to the snakefile, which should be `snakefile.smk` |
| --cores | The number of CPU cores you'd like to run on. |
| --configfile | The path to the configfile you're using. Defaults to `config.yaml` if not provided. |

Put it together, your command will look like the following:

```
snakemake -s snakefile.smk --cores 1 --configfile path/to/your_config.yaml
```

Note that your configfile can be supplied by any filepath, relative or absolute.

**As long as you use separate config files between executions, it is safe to run several processes simultaneously.**

### The `--unlock` Flag:
Sometimes you won't be able to execute the snakefile and you'll get an error telling you to unlock the DAG (DAG is the workflow as a directed acyclic graph). This may happen if the process unexpectedly stops (such as timing out on a HPC cluster) and no error is reciprocated back to the snakefile. In order to solve this, you'll need to run a command similar to the following:

```
snakemake -s snakefile.smk --unlock
```

## Running an Algorithm Component in Isolation
Sometimes you don't want to run the full pipeline but rather just a specific algorithm step. There's two ways to do this:

### Using the driver script (RECOMMENDED)
We recommend executing specific algorithm components using their corresponding driver script in `VAREID/drivers/` for the simplicity of user input and consistent logging with a pipeline execution. **We highly recommend staying consistent with the formatting standards layed out by `config.yaml`!** This way, it's extremely easy to switch between executing stages via the pipeline and separately.

Driver scripts require a configfile structured like `config.yaml`. Once again, your configfile can be supplied by any filepath, relative or absolute.

Since the pipeline was installed as a module, you can easily execute the driver script through this module. No matter what directory you execute from, the path to the driver script will be the same (and relative to VAREID).
```
python -m VAREID.drivers.[driver_script] --config_path path/to/your_config.yaml
```
Notice that we didn't include the `.py` extension on the driver. This is because we're referencing it as a module. Think of this like an import statement, `import VAREID.drivers.[driver_script]`, but you're executing it as a script.

### Using the algorithm component itself
If you don't have a full configfile filled out or would rather not rely on it, you can directly execute each algorithm component using its executable script. Each algorithm component has a separate set of parameters documented with `argparse` Please follow these parameters for your desired component and supply the necessary paths, flags, etc.

Here is an example on how to run frame_sampling.py:

```
python -m VAREID.algo.frame_sampling.frame_sampling \
path/to/ia_filtered_annots.json \
  path/to/fs_annots.json \
  --json_stage1 path/to/stage1_fs_annots.json
```

## Running the Postprocessing Step
Postprocessing is not ran by the pipeline because it requires human interaction to resolve conflicts. To run postprocessing, you can use a driver (see **Running the Postprocessing Step** above). This driver runs the postprocessing script, waits for a SQLite database file to be created, and then opens a GUI. The GUI will checks the database file until conflicts are posted. Your job is to resolve these.

To run the postprocessing driver, use the following:
```
python -m VAREID.drivers.post_driver --config_path path/to/your_config.yaml
```
Wait for a prompt to open a web browser. This is the GUI. Once opened, you'll see a screen similar to the following:

![GUI Screen](https://github.com/user-attachments/assets/153efa29-cba5-4677-8157-f7c61f7019ec)
Use the GUI to resolve all conflicts. It will constantly refresh to check whether conflicts have been saved to the database file. Once all conflicts are resolved, the postprocessing script will end and automatically close the GUI.

### Finishing resolution later
When working with large datasets with many conflicts to resolve, you may have to stop filling out conflicts and come back later. All conflicts and their resolution status are saved to the database file, which **is not reset** on a new call to `post_driver.py`. Thus, you can simply rerun the driver and pick up where you left off.

### Executing without the driver script
If your output formatting is inconsistent with the pipeline you'll need to manually execute two scripts found in `VAREID/algo/postprocessing/`. These are `postprocessing.py` and `gui.py`. Please check their `argparse` parameters for more details.

You will need to execute `postprocessing.py` first and wait until it blocks on user input. For the database (GUI) method, this will look like the following:
```
Still waiting for cluster pair 1 - 0 - Checking again in 5 seconds...
```
At this point, start up `gui.py`.

## Executing tools or any other scripts
Please see the documentation in these scripts, which is usually done via `argparse`.

---

### License
- MIT [![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

  
## References

### Links to related resources (libraries, tools, etc.) or external documentation
* [YOLOv10](https://github.com/THU-MIG/yolov10)
* [BioCLIP](https://github.com/Imageomics/bioclip)
* [MiewID](https://github.com/WildMeOrg/wbia-plugin-miew-id)
* [LCA](https://github.com/WildMeOrg/lca)

   
## Acknowledgements

* **National Science Foundation (NSF)** funded AI institute for Intelligent Cyberinfrastructure with Computational Learning in the Environment (ICICLE) (OAC 2112606).
* **Imageomics Institute (A New Frontier of Biological Information Powered by Knowledge-Guided Machine Learning)** is funded by the US National Science Foundation's Harnessing the Data Revolution (HDR) program under Award (OAC 2118240).
* Support from **Rensselaer Polytechnic Institute (RPI)**.
* Support from **Finnish Cultural Foundation**.
* Resources from **Ohio Supercomputer Center** made it possible to train and test algorithmic components.

## Citation

Ankit K. Upadhyay, Ekaterina Nepovinnykh, S. M. Rayeed, Aidan Westphal, Lawrence Miao, Julian Bain, Jaeseok Kang, Tuomas Eerola, Heikki Kälviäinen, Charles V. Stewart. *Animal Re-Identification via Multiview Spatio-Temporal Track Clustering*. Rensselaer Polytechnic Institute, LUT University, Brno University of Technology, CV4Animals, CVPR 2025.


