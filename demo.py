import parse_exif
import select_images
import write_metadata

def generate_metadata(image_directory="photos", metadata_template_path="template.csv", output_path="output.csv", include_percentile=.5, hierarchy=2):
    filter_results = select_images.filter_images_by_rating(image_directory, percentile=include_percentile, include_unrated_in_result=True)
    image_paths = filter_results.selected
    image_metadata = []

    for path in image_paths:
        all_metadata = parse_exif.read_all_metadata(path)
        condensed_metadata = parse_exif.condense_metadata(all_metadata)
        augmented_metadata = parse_exif.augment_condensed_metadata(path, condensed_metadata, hierarchy)
        image_metadata.append(augmented_metadata)

    write_metadata.append_records_to_csv(metadata_template_path, output_path, image_metadata)    

generate_metadata(include_percentile=0)