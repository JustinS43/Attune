"""Light-ASD, vendored for inference only (V-22).

Source: https://github.com/Junhua-Liao/Light-ASD (commit ed38c232), MIT License
(Copyright (c) 2023 Liao Junhua; see LICENSE in this folder). Paper: Liao et al., "A
Light Weight Model for Active Speaker Detection", CVPR 2023.

Only the files inference needs are here: the encoders, the back-end GRU, the model
and the speaking head. The weights (`finetuning_TalkSet.model`) are not in the repo;
`scripts/download_models.py light_asd` fetches them into models/light_asd/.
Attune's own code around it (features, crops, the scoring thread) is in
`attune/vision/asd.py`.
"""

from .loss import lossAV
from .model import ASD_Model

__all__ = ["ASD_Model", "lossAV"]
