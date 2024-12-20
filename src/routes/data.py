from fastapi import FastAPI, APIRouter, Depends, UploadFile, status, Request
from fastapi.responses import JSONResponse
import os
from helpers.config import Settings, get_settings
from controllers import DataController, ProjectController, ProcessController
from models import ResponseSignal
import aiofiles
import logging
from .schemes.data import ProcessRequest
from models.ProjectModel import ProjectModel
from models.ChunkModel import ChunkModel
from models.AssetModel import AssetModel
from models.db_schemes import DataChunk, Asset
from bson.objectid import ObjectId
from models.enums.AssetTypeEnum import AssetTypeEnum

logger = logging.getLogger('uvicorn.error')


data_router = APIRouter(
    prefix="/api/v1/data",
    tags=["api_v1", "data"]
)



@data_router.post("/upload/{email}/{project_id}")
async def upload_data(request : Request, project_id : str, email : str, file : UploadFile,
                      app_settings : Settings = Depends(get_settings)):
    
    
    project_model = await ProjectModel.create_instance(
        db_client= request.app.db_client
    )


    project = await project_model.get_project_or_create_one(
        project_id = project_id,
        email = email
    )
    
    
    #validate file properties
    data_controller = DataController()
    is_valid, result_signal = data_controller.validate_uploaded_file(file=file)

    if not is_valid:
        return JSONResponse(
            status_code = status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : "result_signal"
            }
        )

    project_dir_path = ProjectController().get_project_path(project_id=project_id)

    file_path, file_id = data_controller.generate_unique_filename(
        orig_filename = file.filename,
        project_id = project_id
    )

    try:
        async with aiofiles.open(file_path, 'wb') as f:
            while chunk := await file.read(app_settings.FILE_DEFAULT_CHUNK_SIZE):
                await f.write(chunk)
    except Exception as e:
        logger.error(f'Error occured while uploading file : {e}')
        return JSONResponse(
            status_code= status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : ResponseSignal.FILE_UPLOAD_FAILED.value
            }
        )

    # store the assets into the database
    asset_model = await AssetModel.create_instance(
        db_client = request.app.db_client
     )
    
    asset_ressource = Asset(
        asset_project_id = project.id,
        asset_type = AssetTypeEnum.FILE.value,
        asset_name = file_id,
        asset_size = os.path.getsize(file_path)
    )

    asset_record = await asset_model.create_asset(asset_ressource)

    return JSONResponse(
            content={
                "signal" : ResponseSignal.FILE_UPLOAD_SUCCESS.value,
                "file_id" : str(asset_record.id),
            }
        )


@data_router.post("/process/{email}/{project_id}")
async def process_endpoint(request : Request, project_id : str, email : str, process_request : ProcessRequest):
    
    chunk_size = process_request.chunk_size
    overlap_size = process_request.overlap_size
    do_reset = process_request.do_reset

    
    
    project_model = await ProjectModel.create_instance(
        db_client= request.app.db_client
    )


    project = await project_model.get_project_or_create_one(
        project_id = project_id,
        email = email
    )

    asset_model = await AssetModel.create_instance(
        db_client = request.app.db_client
        )


    project_files_id = {}
    if process_request.file_id:
        asset_record = await asset_model.get_asset_record(project.id, process_request.file_id)
        if asset_record is None:
            return JSONResponse(
            status_code= status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : ResponseSignal.FILE_ID_ERROR.value,
            }
        )

        project_files_id = {
            asset_record.id : asset_record.asset_name
        }
    else:
        

        project_files = await asset_model.get_all_project_assets(
            asset_project_id = project.id,
            asset_type = AssetTypeEnum.FILE.value
        )

        project_files_id = {
            record.id : record.asset_name
            for record in project_files
        }

    if len(project_files_id) == 0:
        return JSONResponse(
            status_code= status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : ResponseSignal.NO_FILES_ERROR.value,
            }
        )
    

    chunk_model = await ChunkModel.create_instance(
            db_client = request.app.db_client
        )
    
    if do_reset == True:
            _ = await chunk_model.delete_chunks_by_project_id(
                project_id = project.id
            )

    
    process_controller = ProcessController(project_id)
    nb_records = 0
    nb_file = 0
    for asset_id, file_id in project_files_id.items():



        file_content = process_controller.get_file_content(file_id)

        if file_content is None:
            logger.error(f'Error while processing file : {file_id}')
            continue

        file_chunks = process_controller.process_file_content(
            file_content, 
            file_id, 
            chunk_size,
            overlap_size
            )

        if file_chunks is None or len(file_chunks) == 0:
            return JSONResponse(
                content={
                    "signal" : ResponseSignal.PROCESSING_FAILED.value,
                }
            )

        file_chunks_records = [
            DataChunk(
                chunk_text = chunk.page_content,
                chunk_metadata = chunk.metadata,
                chunk_order = i+1,
                chunk_project_id = project.id,
                chunk_asset_id = asset_id
            )
            for i, chunk in enumerate(file_chunks)
        ]

        nb_records += await chunk_model.insert_many_chunks(chunks = file_chunks_records)
        nb_file += 1

    return JSONResponse(
            content={
                "signal" : ResponseSignal.PROCESSING_SUCCESS.value,
                "inserted_chunks" : nb_records,
                "processed_files" : nb_file
            }
        )



@data_router.get("/process/{email}")
async def projects_endpoint(request : Request, email : str):

    project_model = await ProjectModel.create_instance(
        db_client= request.app.db_client
    )

    results = await project_model.get_project_by_user(email = email)

    if results is None or len(results) == 0:
        return JSONResponse(
            status_code= status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : ResponseSignal.NO_PROJECTS_ERROR.value
            }
        )
    
    return JSONResponse(
            content={
                "signal" : ResponseSignal.PROJECTS_FOUND.value,
                "results" : [res.project_id for res in results]
            }
        )
