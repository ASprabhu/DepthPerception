import os
import sys
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.cm import get_cmap
import configuration as config
import torch
import time
from cv2 import cv2

sys.path.append("/home/ubuntu/18744/DepthPerception/Code/packnet-sfm/")
from packnet_sfm.models.model_wrapper import ModelWrapper
from packnet_sfm.datasets.augmentations import resize_image, to_tensor
from packnet_sfm.utils.horovod import hvd_init, rank, world_size, print0
from packnet_sfm.utils.image import load_image
from packnet_sfm.utils.config import parse_test_file
from packnet_sfm.utils.load import set_debug
from packnet_sfm.utils.depth import write_depth, inv2depth, viz_inv_depth
from packnet_sfm.utils.logging import pcolor


def project_disp_to_points(calib, disp, max_high):
    disp[disp < 0] = 0
    baseline = 0.54
    mask = disp > 0
    depth = calib.f_u * baseline / (disp + 1. - mask)
    rows, cols = depth.shape
    c, r = np.meshgrid(np.arange(cols), np.arange(rows))
    points = np.stack([c, r, depth])
    points = points.reshape((3, -1))
    points = points.T
    points = points[mask.reshape(-1)]
    cloud = calib.project_image_to_velo(points)
    valid = (cloud[:, 0] >= 0) & (cloud[:, 2] < max_high)
    return cloud[valid]

def project_depth_to_points(calib, depth, max_high):
    rows, cols = depth.shape
    c, r = np.meshgrid(np.arange(cols), np.arange(rows))
    points = np.stack([c, r, depth])
    points = points.reshape((3, -1))
    points = points.T
    pseudo_cloud_rect = calib.project_image_to_rect(points)
    cloud = calib.project_rect_to_velo(pseudo_cloud_rect)
    valid = (cloud[:, 0] >= 0) & (cloud[:, 2] < max_high)
    return pseudo_cloud_rect, cloud[valid]

@torch.no_grad()
def infer_and_save_depth(input_file, output_file, model_wrapper, image_shape, half, save):
    """
    Process a single input file to produce and save visualization

    Parameters
    ----------
    input_file : str
        Image file
    output_file : str
        Output file, or folder where the output will be saved
    model_wrapper : nn.Module
        Model wrapper used for inference
    image_shape : Image shape
        Input image shape
    half: bool
        use half precision (fp16)
    save: str
        Save format (npz or png)
    """
    if not is_image(output_file):
        # If not an image, assume it's a folder and append the input name
        os.makedirs(output_file, exist_ok=True)
        output_file = os.path.join(output_file, os.path.basename(input_file))

    # change to half precision for evaluation if requested
    dtype = torch.float16 if half else None

    # Load image
    image = load_image(input_file)
    # Resize and to tensor
    image = resize_image(image, image_shape)
    image = to_tensor(image).unsqueeze(0)

    # Send image to GPU if available
    if torch.cuda.is_available():
        image = image.to('cuda:{}'.format(rank()), dtype=dtype)

    # Depth inference (returns predicted inverse depth)
    pred_inv_depth = model_wrapper.depth(image)['inv_depths'][0]

    if save == 'npz' or save == 'png' or save == 'npy':
        # Get depth from predicted depth map and save to different formats
        filename = '{}.{}'.format(os.path.splitext(output_file)[0], save)
        print('Saving {} to {}'.format(
            pcolor(input_file, 'cyan', attrs=['bold']),
            pcolor(filename, 'magenta', attrs=['bold'])))
        depth_npy = inv2depth(pred_inv_depth)
        write_depth(filename, depth=inv2depth(pred_inv_depth))
        return depth_npy
    else:
        # Prepare RGB image
        rgb = image[0].permute(1, 2, 0).detach().cpu().numpy() * 255
        # Prepare inverse depth
        viz_pred_inv_depth = viz_inv_depth(pred_inv_depth[0]) * 255
        # Concatenate both vertically
        image = np.concatenate([rgb, viz_pred_inv_depth], 0)
        # Save visualization
        print('Saving {} to {}'.format(
            pcolor(input_file, 'cyan', attrs=['bold']),
            pcolor(output_file, 'magenta', attrs=['bold'])))
        imwrite(output_file, image[:, :, ::-1])
        return image[:, :, ::-1]

def process_image(image, folder, frame_id, depth_model, show_image = True):
    i = np.array(image.raw_data)
    i2 = i.reshape((config.IMHEIGHT, config.IMWIDTH, 4))
    i3 = i2[:, :, :3]
    depth_map = depth_model.generate_depth_map(i3)
    depth_map= ((depth_map/255) * 255).astype('uint8')
    # depth_map = -depth_map + 255
    
    depth_map_img = cv2.cvtColor(depth_map, cv2.COLOR_RGB2BGR)
    # rgb = cv2.cvtColor(i3, cv2.COLOR_BGR2RGB)#np.transpose(i3, (1, 2, 0))
    # viz_pred_inv_depth = viz_inv_depth(depth_map) * 255
    # image_viz = np.concatenate([rgb, viz_pred_inv_depth], 0)
    # depth_map_img = np.dstack((depth_map, depth_map, depth_map))
    # depth_map_img = depth_map
    # depth_map_img = ((depth_map_img/255) * 255).astype('uint8')
    # print(np.shape(depth_map_img))
    depth_map_img_gray = cv2.applyColorMap(depth_map_img, cv2.COLORMAP_MAGMA)
    #depth_map_img_gray = cv2.cvtColor(depth_map_img, cv2.COLOR_BGR2RGB)
    # concat_image = np.concatenate((i3_gray, depth_map_img_gray), axis=1)
    if(show_image == True):
      
        cv2.imshow("Original image", i3)
        cv2.imshow("Depth map", depth_map_img_gray)
        cv2.waitKey(5)
    if(folder is not None):

        file_name = 'image_{}.jpg'.format(str(frame_id))
        file = os.path.join(folder, file_name)
        cv2.imwrite(file, i3)
    return i3/255.0

