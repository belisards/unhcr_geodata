import streamlit as st
from typing import Optional, Dict, Any, List
from shapely.geometry import Point, mapping, shape
import os
import requests
import json
import folium
import pandas as pd
from streamlit_folium import st_folium
from pyproj import Transformer

# Constants
BASE_URL: str = "https://gis.unhcr.org/arcgis/rest/services/core_v2/"
COMMON_PARAMS: Dict[str, str] = {'f': 'geojson'}
EXPORT_FOLDER: str = "data"
session: requests.Session = requests.Session()

# Function Definitions
def setup_folder(folder: str) -> None:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Setup folder
setup_folder(EXPORT_FOLDER)

def list_countries() -> List[str]:
    params: Dict[str, str] = {**COMMON_PARAMS, 'where': "1=1", 'outFields': '*', 'returnGeometry': 'false'}
    try:
        response = session.get(BASE_URL + "wrl_prp_a_unhcr/FeatureServer/0/query", params=params)
        response.raise_for_status()
        data: Dict[str, Any] = response.json()
        site_codes: List[str] = [item["properties"]["site_code"][:3] for item in data.get("features", [])]
        country_codes: List[str] = sorted(list(set(site_codes)))
        return country_codes
    except requests.RequestException as e:
        st.error(f"Failed to fetch data: {e}")
        return []
    

def extract_site_codes(country_data: Dict[str, Any]) -> List[str]:
    """
    Extract site codes from country data.

    :param country_data: GeoJSON-like structure containing country features.
    :return: A list of site codes.
    """
    results: List[str] = [item["properties"]["site_code"] for item in country_data.get("features", [])]
    return results

## QUERY POINTS

def query_points(country_code: str, site_codes: List[str]) -> Dict[str, Any]:
    """
    Query points data for a given country code, excluding specific site codes.

    :param country_code: The ISO3 country code to filter by.
    :param site_codes: List of site codes to exclude from the query.
    :return: GeoJSON-like data containing point features.
    """
    site_codes_quoted: List[str] = [f"'{code}'" for code in site_codes]
    where_clause: str = f"iso3='{country_code}' AND pcode NOT IN ({','.join(site_codes_quoted)})"
    url: str = f"{BASE_URL}wrl_prp_p_unhcr_PoC/FeatureServer/0/query"
    params: Dict[str, str] = {
        'where': where_clause,
        'outFields': 'pcode,gis_name',
        'f': 'geojson',
        'returnGeometry': 'true'
    }
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        data: Dict[str, Any] = response.json()
        # Add feature type
        for feature in data.get("features", []):
            feature['properties']['feature_type'] = 'Point'
        return data
    except requests.RequestException as e:
        print(f"Failed to fetch data: {e}")
        return {}

### GET OFFICIAL POLYGONS
def query_polygons(country_code: str,buffer_size_poly: float) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        'where': f"site_code LIKE '{country_code}%'",
        'outFields': 'site_code, name',
        'f': 'geojson',
        'returnGeometry': 'true',
        'geometryType': 'esriGeometryPolygon',
        'outSR': 4326,
    }
    try:
        response = session.get(BASE_URL + "wrl_prp_a_unhcr/FeatureServer/0/query", params=params)
        response.raise_for_status()
        data: Dict[str, Any] = response.json()
        # Add buffer to each geometry
        for feature in data.get('features', []):
            geometry = shape(feature['geometry'])
            buffered_geometry = geometry.buffer(buffer_size_poly)
            feature['geometry'] = mapping(buffered_geometry)
            feature['properties']['feature_type'] = 'Polygon'

        return data
    except requests.RequestException as e:
        st.error(f"Failed to fetch data: {e}")
        return {}


def gen_polygons(data: Dict[str, Any], buffer_size: float = 0.01) -> Dict[str, Any]:
    """
    Generates buffered polygons for each feature in the geojson-like data.

    :param data: The input data containing features with geometry of type 'Point'.
    :param buffer_size: The buffer size (in coordinate units) to be used for generating polygons.
    :return: A GeoJSON-like structure with polygons around each point.
    """
    features: List[Dict[str, Any]] = data.get('features', [])
    buffered_features: List[Dict[str, Any]] = []

    for feature in features:
        point_coords: List[float] = feature['geometry']['coordinates']
        point = Point(point_coords)

        # Create a buffer around the point (optionally change to use projected buffer if needed)
        buffer = point.buffer(buffer_size)

        buffered_feature: Dict[str, Any] = {
            'type': 'Feature',
            'geometry': mapping(buffer),  # Use mapping to convert Shapely geometry to GeoJSON format
            'properties': feature['properties']
        }
        buffered_features.append(buffered_feature)

    buffered_geojson: Dict[str, Any] = {
        'type': 'FeatureCollection',
        'features': buffered_features
    }

    assert len(buffered_geojson['features']) == len(features)
    return buffered_geojson

def process_country(country_code: str, buffer_size_points: float, buffer_size_poly: float) -> Optional[Dict[str, Any]]:
    """
    Process the country data by generating polygons and merging them with existing polygons.

    :param country_code: The ISO3 code of the country to process.
    :param buffer_size_points: The size of the buffer to generate polygons from points.
    :return: A combined GeoJSON structure of polygons and generated polygons.
    """
    # Get polygons
    official_polygons: Dict[str, Any] = query_polygons(country_code,buffer_size_poly)
    if not official_polygons:
        print("No data found for the country")
        return None
    else:
        n_polygons = len(official_polygons['features'])
        print(f"Successfully fetched {n_polygons} official polygons")
    
    # Get points
    site_codes: List[str] = extract_site_codes(official_polygons)
    points_data: Optional[Dict[str, Any]] = query_points(country_code, site_codes)

    if not points_data or not points_data.get("features"):
        print("No points data found")
        n_points = 0
    else:
        n_points = len(points_data['features'])
        print(f"Successfully fetched {n_points} points")
    
    # Generate polygons from points
    generated_polygons: Dict[str, Any] = gen_polygons(points_data, buffer_size_points)
        
    # Merge polygons
    country_polygons: List[Dict[str, Any]] = official_polygons["features"]
    
    # Add generated polygons if they exist
    if generated_polygons.get("features"):
        country_polygons.extend(generated_polygons["features"])
    
    country_data: Dict[str, Any] = {
        "type": "FeatureCollection",
        "features": country_polygons
    }
    
    return country_data, n_polygons, n_points


### get dates

def convert_esri_feature_to_geojson(esri_feature):
    """
    Convert ESRI Feature to GeoJSON format
    """
    try:
        geojson_feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": []
            },
            "properties": esri_feature.get('attributes', {})
        }
        
        if 'geometry' in esri_feature and 'rings' in esri_feature['geometry']:
            geojson_feature['geometry']['coordinates'] = esri_feature['geometry']['rings']
            
        return geojson_feature
    except Exception as e:
        st.error(f"Error converting ESRI feature to GeoJSON: {str(e)}")
        return None

def get_imagery_dates(bounds, zoom_level):
    """
    Query ESRI World Imagery service for image dates within the given bounds.
    """
    if zoom_level < 12:
        st.sidebar.info("Please zoom in to level 12 or higher to see imagery dates.")
        return {}
        
    base_url = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/0/query"
    
    params = {
        'f': 'json',
        'spatialRel': 'esriSpatialRelIntersects',
        'geometry': json.dumps({
            'xmin': bounds[0],
            'ymin': bounds[1],
            'xmax': bounds[2],
            'ymax': bounds[3],
            'spatialReference': {'wkid': 102100}
        }),
        'geometryType': 'esriGeometryEnvelope',
        'inSR': 102100,
        'outSR': 3857,
        'outFields': '*',
        'returnGeometry': True
    }
    
    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        data = response.json()
        
        if 'features' not in data:
            st.sidebar.error("No imagery data received from the server.")
            return {}
            
        dates_dict = {}
        for feature in data['features']:
            if 'attributes' in feature and 'SRC_DATE' in feature['attributes']:
                date_str = str(feature['attributes']['SRC_DATE'])
                formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                geojson_feature = convert_esri_feature_to_geojson(feature)
                if geojson_feature:
                    dates_dict[formatted_date] = geojson_feature
        # print(dates_dict.keys())
        return dates_dict.keys()
        
    except requests.exceptions.RequestException as e:
        st.sidebar.error(f"Error fetching imagery dates: {str(e)}")
        return {}

def initialize_session_state():
    for key, default in {
        'imagery_dates': [],
        'previous_country_code': None,
        'country_data': None,
        'n_polygons': None,
        'n_points': None,
        'feature_count': None,
        'map_data': None
    }.items():
        if key not in st.session_state:
            st.session_state[key] = default

#####################################
# Streamlit App
st.title("UNHCR Geodata Extractor")

# # Store the previous country code in session state to detect changes
# if 'previous_country_code' not in st.session_state:
#     st.session_state['previous_country_code'] = None

# Initialize session state
initialize_session_state()

country_list: List[str] = list_countries()

country_code = st.sidebar.selectbox("Select a country:", country_list)


# Clear sidebar info if the country_code has changed
if st.session_state['previous_country_code'] != country_code:
    if 'country_data' in st.session_state:
        st.session_state.pop('country_data', None)
        st.session_state.pop('n_polygons', None)
        st.session_state.pop('n_points', None)
        st.session_state.pop('feature_count', None)
    st.session_state['previous_country_code'] = country_code

buffer_size_points = st.sidebar.slider("Select buffer size for points", min_value=0.001, max_value=0.1, value=0.01, step=0.001)

buffer_size_poly = st.sidebar.slider("Select buffer size for polygons", min_value=0.001, max_value=0.1, value=0.0, step=0.001)

# remove sidebarinfo if country_code changes

if st.sidebar.button("Load country"):
    if country_code:
        st.write(f"Processing country: {country_code} with buffer size for points: {buffer_size_points}")
        country_data, n_polygons, n_points = process_country(country_code, buffer_size_points,buffer_size_poly)
        if country_data:
            st.session_state['country_data'] = country_data
            st.session_state['n_polygons'] = n_polygons
            st.session_state['n_points'] = n_points
            # print(type(country_data))
            st.session_state['feature_count'] = n_points + n_polygons
    else:
        st.warning("Please select a country")

# Display Features and Export Option
if 'country_data' in st.session_state:
    features = st.session_state['country_data']['features']
    st.sidebar.info(
        f"Number of features for {country_code}: {st.session_state['feature_count']}  \n"
        f"Points: {st.session_state['n_points']}  \n"
        f"Polygons: {st.session_state['n_polygons']}"
    )

    # Display all features as a single list if filtering is not applicable
    all_feature_labels = [f"{feature['properties'].get('name', 'N/A')} ({feature['properties'].get('feature_type', 'N/A')})" for feature in features]
    # filter only records with Polygon in the name
    polygon_feature_labels = [feature for feature in all_feature_labels if 'Polygon' in feature]

    # Feature selection for viewing details
    selected_label = st.selectbox("Select a feature to view details:", all_feature_labels)
    
    # Check if a valid selection was made
    selected_feature = next((feature for feature, label in zip(features, all_feature_labels) if label == selected_label), None)
    
    # Only proceed if a feature was found
    if selected_feature is not None:
        # Extract geometry from selected feature
        selected_feature_geometry = selected_feature['geometry']
        geometry = shape(selected_feature_geometry)
        centroid = shape(selected_feature_geometry).centroid
        # lat lon
        centroid = (centroid.y, centroid.x)
        print(centroid)
        
        # Create and display map
        m = folium.Map(location=centroid, zoom_start=13, width='100%', height='700')
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='ArcGIS World Imagery'
        ).add_to(m)

        # Add all features to the map
        for feature in features:
            if feature['geometry']['type'] in ['Polygon', 'MultiPolygon']:
                # Highlight selected feature
                style = {'color': 'red', 'weight': 3} if feature == selected_feature else {'color': 'blue', 'weight': 2}
                folium.GeoJson(
                    feature,
                    style_function=lambda x, style=style: style
                ).add_to(m)
        
        st.session_state.map_data = st_folium(m, width=1200, height=800)#, returned_objects=[])
        
        # display imagery dates
        bounds = st.session_state.map_data['bounds']
        
        # Check if map_data and bounds exist
        if st.session_state.map_data and 'bounds' in st.session_state.map_data:
            bounds = st.session_state.map_data['bounds']
            zoom_level = st.session_state.map_data['zoom']
            if zoom_level >= 12 and bounds.get('_southWest') and bounds.get('_northEast'):
                transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
                sw_lng = bounds['_southWest'].get('lng')
                sw_lat = bounds['_southWest'].get('lat')
                ne_lng = bounds['_northEast'].get('lng')
                ne_lat = bounds['_northEast'].get('lat')
                if None not in (sw_lng, sw_lat, ne_lng, ne_lat):
                    sw_x, sw_y = transformer.transform(
                        float(sw_lng),
                        float(sw_lat)
                    )
                    ne_x, ne_y = transformer.transform(
                        float(ne_lng),
                        float(ne_lat)
                    )
                    dates = get_imagery_dates((sw_x, sw_y, ne_x, ne_y), zoom_level)
                    if dates:
                        st.session_state.imagery_dates = ", ".join(dates)
                        st.sidebar.write(f"Imagery dates: {st.session_state.imagery_dates}")

                else:
                    st.sidebar.write(f"Current zoom level: {zoom_level} - Imagery dates are only available at zoom level 12 or higher.")

                

    
    else:
        st.warning("Please select a valid feature to view details.")

    # Fix renaming
    for feature in features:
        if 'pcode' in feature['properties']:
            feature['properties']['site_code'] = feature['properties'].pop('pcode')
        if 'gis_name' in feature['properties']:
            feature['properties']['name'] = feature['properties'].pop('gis_name')

    # Select features to export
    st.write("### Select features to export")

    # Checkbox to select all features
    select_all = st.checkbox("Select all")

    # Checkbox to select only polygon features
    select_polygons_only = st.checkbox("Select polygons only")

    # Determine the default selection based on checkboxes
    if select_all:
        default_selection = all_feature_labels
    elif select_polygons_only:
        # print(polygon_feature_labels)
        default_selection = polygon_feature_labels
    else:
        default_selection = [] 

    # Multi-select for selecting features to export
    selected_features_to_export = st.multiselect(
        "Select features to export:",
        options=all_feature_labels,  # Static list of all options
        default=default_selection           # Dynamic default selection
    )

# Download selected features as GeoJSON and CSV with Bounding Boxes
    if st.button("Export data"):
        if not selected_features_to_export:
            st.error("No feature selected. Please select at least one feature.")
        else:
            # Filter features to export
            filtered_features = [
                feature for feature, label in zip(features, all_feature_labels)
                if label in selected_features_to_export
            ]

            # Prepare filtered GeoJSON data
            filtered_data = {
                "type": "FeatureCollection",
                "features": filtered_features
            }

            # Write GeoJSON to a temporary file
            filtered_output_file = f"{EXPORT_FOLDER}/{country_code}_filtered_polygons.geojson"
            with open(filtered_output_file, 'w') as f:
                json.dump(filtered_data, f, indent=4)

            # Display success message
            st.success(f"Exported {len(filtered_features)} features")

            # Convert GeoJSON to download button
            with open(filtered_output_file, 'r') as f:
                st.download_button(
                    label="Download GeoJSON (Polygons)",
                    data=f,
                    file_name=f"{country_code}_filtered_polygons.geojson",
                    mime="application/geo+json"
                )

            # Prepare bounding box data for each feature
            bounding_boxes = []
            for feature in filtered_features:
                # print(feature['properties'])
                feature_id = feature['properties'].get('site_code', 'unknown')
                feature_type = feature['properties'].get('feature_type', 'unknown')
                feature_name = feature['properties'].get('name', 'unknown')
                geometry = shape(feature['geometry'])

                # Calculate bounding box
                min_x, min_y, max_x, max_y = geometry.bounds
                bounding_boxes.append({
                    "id": feature_id,
                    "feature_type": feature_type,
                    "feature_name": feature_name,
                    "min_x": min_x,  # min longitude
                    "min_y": min_y,  # min latitude
                    "max_x": max_x,  # max longitude
                    "max_y": max_y   # max latitude
                })

            # Create DataFrame using Pandas
            df = pd.DataFrame(bounding_boxes)

            # Convert DataFrame to CSV and provide download button
            csv_data = df.to_csv(index=False)
            st.download_button(
                label="Download CSV (Bounding Box)",
                data=csv_data,
                file_name=f"{country_code}_bounding_boxes.csv",
                mime="text/csv"
            )