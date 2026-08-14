# Data Preparation Tools

These scripts are helper tools for dataset preparation and experiments:

- `labelme_to_yolo.py`: convert LabelMe JSON annotations to YOLO labels.
- `polygon_labels_to_bbox.py`: convert YOLO polygon labels to bbox labels.
- `augment_dataset.py`: create augmented training image/label pairs.
- `convert_images_to_grayscale.py`: convert images to 3-channel grayscale.
- `integrate_field_to_dataset.py`: copy LabelMe-annotated field images into deterministic train/validation splits.
- `build_tuned_dataset.py`: build a field-weighted training dataset without changing the original dataset.
- `balance_single_class_augmented.py`: add augmented samples for an under-represented class.
