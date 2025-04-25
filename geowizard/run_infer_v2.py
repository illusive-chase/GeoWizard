# Adapted from Marigold ：https://github.com/prs-eth/Marigold

import argparse
import logging
import os

import numpy as np
import torch
from diffusers import AutoencoderKL, DDIMScheduler
from PIL import Image
from transformers import CLIPTextModel, CLIPTokenizer

from models.geowizard_v2_pipeline import DepthNormalEstimationPipeline
from models.unet_2d_condition import UNet2DConditionModel
from utils.depth2normal import *
from utils.seed_all import seed_all

if __name__=="__main__":
    
    logging.basicConfig(level=logging.INFO)
    
    '''Set the Args'''
    parser = argparse.ArgumentParser(
        description="Run MonoDepthNormal Estimation using Stable Diffusion."
    )
    parser.add_argument(
        "--pretrained_model_path",
        type=str,
        default='lemonaddie/geowizard',
        help="pretrained model path from hugging face or local dir",
    )    
    parser.add_argument(
        "--input_file", type=str, required=True, help="Input file."
    )

    parser.add_argument(
        "--output_dir", type=str, required=True, help="Output directory."
    )
    parser.add_argument(
        "--domain",
        type=str,
        default='object',
        help="domain prediction",
    )   

    # inference setting
    parser.add_argument(
        "--denoise_steps",
        type=int,
        default=10,
        help="Diffusion denoising steps, more steps results in higher accuracy but slower inference speed.",
    )
    parser.add_argument(
        "--ensemble_size",
        type=int,
        default=10,
        help="Number of predictions to be ensembled, more inference gives better results but runs slower.",
    )
    parser.add_argument(
        "--half_precision",
        action="store_true",
        help="Run with half-precision (16-bit float), might lead to suboptimal result.",
    )

    # resolution setting
    parser.add_argument(
        "--processing_res",
        type=int,
        default=768,
        help="Maximum resolution of processing. 0 for using input image resolution. Default: 768.",
    )
    parser.add_argument(
        "--output_processing_res",
        action="store_true",
        help="When input is resized, out put depth at resized operating resolution. Default: False.",
    )

    # depth map colormap
    parser.add_argument(
        "--color_map",
        type=str,
        default="Spectral",
        help="Colormap used to render depth predictions.",
    )
    # other settings
    parser.add_argument("--seed", type=int, default=None, help="Random seed.")
    parser.add_argument(
        "--batch_size",
        type=int,
        default=0,
        help="Inference batch size. Default: 0 (will be set automatically).",
    )
    
    args = parser.parse_args()
    
    checkpoint_path = args.pretrained_model_path
    output_dir = args.output_dir
    denoise_steps = args.denoise_steps
    ensemble_size = args.ensemble_size
    
    if ensemble_size>15:
        logging.warning("long ensemble steps, low speed..")
    
    half_precision = args.half_precision

    processing_res = args.processing_res
    match_input_res = not args.output_processing_res
    domain = args.domain

    color_map = args.color_map
    seed = args.seed
    batch_size = args.batch_size
    
    if batch_size==0:
        batch_size = 1  # set default batchsize
    
    # -------------------- Preparation --------------------
    # Random seed
    if seed is None:
        import time
        seed = int(time.time())
    seed_all(seed)

    logging.info(f"output dir = {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    # -------------------- Device --------------------
    if torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")
        logging.warning("CUDA is not available. Running on CPU will be slow.")
    logging.info(f"device = {device}")

    # -------------------- Model --------------------
    if half_precision:
        dtype = torch.float16
        logging.info(f"Running with half precision ({dtype}).")
    else:
        dtype = torch.float32

    # declare a pipeline
    stable_diffusion_repo_path = "stabilityai/stable-diffusion-2"
    vae = AutoencoderKL.from_pretrained(stable_diffusion_repo_path, subfolder='vae')
    text_encoder = CLIPTextModel.from_pretrained(stable_diffusion_repo_path, subfolder='text_encoder')
    scheduler = DDIMScheduler.from_pretrained(stable_diffusion_repo_path, subfolder='scheduler')
    tokenizer = CLIPTokenizer.from_pretrained(stable_diffusion_repo_path, subfolder='tokenizer')
    unet = UNet2DConditionModel.from_pretrained(checkpoint_path, subfolder='unet_v2')
                
    pipe = DepthNormalEstimationPipeline(vae=vae,
                                text_encoder=text_encoder,
                                tokenizer=tokenizer,
                                unet=unet,
                                scheduler=scheduler)

    logging.info("loading pipeline whole successfully.")
    
    try:
        pipe.enable_xformers_memory_efficient_attention()
    except:
        pass  # run without xformers

    pipe = pipe.to(device)

    # -------------------- Inference and saving --------------------
    with torch.no_grad():

        # Read input image
        input_image = Image.open(args.input_file)

        # predict the depth & normal here
        pipe_out = pipe(input_image,
            denoising_steps = denoise_steps,
            ensemble_size= ensemble_size,
            processing_res = processing_res,
            match_input_res = match_input_res,
            domain = domain,
            color_map = color_map,
            show_progress_bar = True,
        )

        depth_pred: np.ndarray = pipe_out.depth_np
        depth_colored: Image.Image = pipe_out.depth_colored
        normal_pred: np.ndarray = pipe_out.normal_np
        normal_colored: Image.Image = pipe_out.normal_colored

        # Save as npy

        normal_npy_save_path = os.path.join(output_dir, "normal.npy")
        if os.path.exists(normal_npy_save_path):
            logging.warning(f"Existing file: '{normal_npy_save_path}' will be overwritten")
        np.save(normal_npy_save_path, normal_pred)

        normal_colored_save_path = os.path.join(output_dir, "normal.png")
        if os.path.exists(normal_colored_save_path):
            logging.warning(
                f"Existing file: '{normal_colored_save_path}' will be overwritten"
            )
        normal_colored.save(normal_colored_save_path)
