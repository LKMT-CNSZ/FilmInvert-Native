"""Linear primary conversion, shared by input, chart references and display."""
import numpy as np
import colorio

def linear(rgb,source='srgb',target='prophoto'):
    if source==target:return np.asarray(rgb,np.float32)
    adaptation=np.eye(3)
    if (source=='prophoto')!=(target=='prophoto'):
        adaptation=colorio.ADAPT if target=='prophoto' else np.linalg.inv(colorio.ADAPT)
    matrix=np.linalg.inv(colorio.MATRICES[target])@adaptation@colorio.MATRICES[source]
    return np.asarray(rgb,np.float32)@matrix.astype(np.float32).T

def display(rgb,space='srgb'):
    return colorio.encode(linear(rgb,space,'srgb'),'srgb')
