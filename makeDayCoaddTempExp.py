#
# LSST Data Management System
# Copyright 2008, 2009, 2010, 2011, 2012 LSST Corporation.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#

from __future__ import absolute_import, division, print_function
import numpy

import lsst.pex.config as pexConfig
import lsst.afw.image as afwImage
import lsst.coadd.utils as coaddUtils
import lsst.pipe.base as pipeBase
from lsst.meas.algorithms import CoaddPsf
from .coaddBase import CoaddBaseTask
from .warpAndPsfMatch import WarpAndPsfMatchTask
from .coaddHelpers import groupPatchExposures, getGroupDataRef
#???from lsst.coadd.utils import CoaddDataIdContainer
#???from lsst.pipe.tasks.coaddBase import SelectDataIdContainer
from .selectImages import WcsSelectImagesTask, SelectStruct
from lsst.afw.fits import FitsError

__all__ = ["MakeDayCoaddTempExpTask"]


class MakeDayCoaddTempExpConfig(CoaddBaseTask.ConfigClass):
    """Config for MakeDayCoaddTempExpTask
    """
    warpAndPsfMatch = pexConfig.ConfigurableField(
        target=WarpAndPsfMatchTask,
        doc="Task to warp and PSF-match calexp",
    )
    doWrite = pexConfig.Field(
        doc="persist <coaddName>Coadd_tempExp",
        dtype=bool,
        default=True,
    )
    doOverwrite = pexConfig.Field(
        doc="overwrite <coaddName>Coadd_tempExp; If False, continue if the file exists on disk",
        dtype=bool,
        default=True,
    )
    bgSubtracted = pexConfig.Field(
        doc="Work with a background subtracted calexp?",
        dtype=bool,
        default=True,
    )

class MakeDayCoaddTaskRunner(pipeBase.TaskRunner):
    @staticmethod
    def getTargetList(parsedCmd, **kwargs):
        return pipeBase.TaskRunner.getTargetList(parsedCmd,  **kwargs)


class MakeDayCoaddTempExpTask(CoaddBaseTask):
    """Task to produce <coaddName>Coadd_tempExp images
    """
    RunnerClass = MakeDayCoaddTaskRunner
    ConfigClass = MakeDayCoaddTempExpConfig
    _DefaultName = "makeDayCoaddTempExp"

    def __init__(self, *args, **kwargs):
        CoaddBaseTask.__init__(self, *args, **kwargs)
        self.makeSubtask("warpAndPsfMatch")

    @pipeBase.timeMethod
    def run(self, patchRef, selectDataList=[]):
        """Produce <coaddName>Coadd_tempExp images

        <coaddName>Coadd_tempExp are produced by PSF-matching (optional) and warping.

        @param[in] patchRef: data reference for sky map patch. Must include keys "tract", "patch",
            plus the camera-specific filter key (e.g. "filter" or "band")
        @return: dataRefList: a list of data references for the new <coaddName>Coadd_tempExp

        @warning: this task assumes that all exposures in a coaddTempExp have the same filter.

        @warning: this task sets the Calib of the coaddTempExp to the Calib of the first calexp
        with any good pixels in the patch. For a mosaic camera the resulting Calib should be ignored
        (assembleCoadd should determine zeropoint scaling without referring to it).
        """
        skyInfo = self.getSkyInfo(patchRef)

	print("--------------------------------------------------DCT1--------")
	miniB = patchRef.getButler()
	
	date =  patchRef.dataId["date"]
	filter =  patchRef.dataId["filter"]

	butler_subset = miniB.subset("calexp", dataId={"filter":filter, "date":date})
        print(butler_subset)

	gen_list = []
	for s in butler_subset:
                if (not s.datasetExists("calexp")):
                    continue
		try:
                        md = s.get("calexp_md", immediate=True)
			wcs = afwImage.makeWcs(md)
			data = SelectStruct(dataRef = s, wcs=wcs, dims=(md.get("NAXIS1"), md.get("NAXIS2")))
			
		except FitsError as e:
			print("Unable to construct Wcs from", (s.dataId))
			continue
		gen_list.append(data)
	
	print("----------------------------DCT2-- NEWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW-----")
	#print selectDataList[0]["dataRef"]
        del miniB 
	

	selectDataList=gen_list


        calExpRefList = self.selectExposures(patchRef, skyInfo, selectDataList=selectDataList)
        if len(calExpRefList) == 0:
            self.log.warn("No exposures to coadd for patch %s", patchRef.dataId)
            return None
        self.log.info("Selected %d calexps for patch %s", len(calExpRefList), patchRef.dataId)
        calExpRefList = [calExpRef for calExpRef in calExpRefList if calExpRef.datasetExists("calexp")]
        self.log.info("Processing %d existing calexps for patch %s", len(calExpRefList), patchRef.dataId)

        groupData = groupPatchExposures(patchRef, calExpRefList, self.getCoaddDatasetName(),
                                        self.getTempExpDatasetName())
        self.log.info("Processing %d tempExps for patch %s", len(groupData.groups), patchRef.dataId)

        dataRefList = []
        for i, (tempExpTuple, calexpRefList) in enumerate(groupData.groups.items()):
            tempExpRef = getGroupDataRef(patchRef.getButler(), self.getTempExpDatasetName(),
                                         tempExpTuple, groupData.keys)
            if not self.config.doOverwrite and tempExpRef.datasetExists(datasetType=self.getTempExpDatasetName()):
                self.log.info("tempCoaddExp %s exists; skipping", tempExpRef.dataId)
                dataRefList.append(tempExpRef)
                continue
            self.log.info("Processing tempExp %d/%d: id=%s", i, len(groupData.groups), tempExpRef.dataId)

            # TODO: mappers should define a way to go from the "grouping keys" to a numeric ID (#2776).
            # For now, we try to get a long integer "visit" key, and if we can't, we just use the index
            # of the visit in the list.
            try:
                visitId = int(tempExpRef.dataId["visit"])
            except (KeyError, ValueError):
                visitId = i

            exp = self.createTempExp(calexpRefList, skyInfo, visitId)
            if exp is not None:
                dataRefList.append(tempExpRef)
                if self.config.doWrite:
                    self.writeCoaddOutput(tempExpRef, exp, "tempExp")
            else:
                self.log.warn("tempExp %s could not be created", tempExpRef.dataId)
        return dataRefList

    def createTempExp(self, calexpRefList, skyInfo, visitId=0):
        """Create a tempExp from inputs

        We iterate over the multiple calexps in a single exposure to construct
        the warp ("tempExp") of that exposure to the supplied tract/patch.

        Pixels that receive no pixels are set to NAN; this is not correct
        (violates LSST algorithms group policy), but will be fixed up by
        interpolating after the coaddition.

        @param calexpRefList: List of data references for calexps that (may)
            overlap the patch of interest
        @param skyInfo: Struct from CoaddBaseTask.getSkyInfo() with geometric
            information about the patch
        @param visitId: integer identifier for visit, for the table that will
            produce the CoaddPsf
        @return warped exposure, or None if no pixels overlap
        """
        inputRecorder = self.inputRecorder.makeCoaddTempExpRecorder(visitId, len(calexpRefList))
        coaddTempExp = afwImage.ExposureF(skyInfo.bbox, skyInfo.wcs)
        coaddTempExp.getMaskedImage().set(numpy.nan, afwImage.MaskU.getPlaneBitMask("NO_DATA"), numpy.inf)
        totGoodPix = 0
        didSetMetadata = False
        modelPsf = self.config.modelPsf.apply() if self.config.doPsfMatch else None
        for calExpInd, calExpRef in enumerate(calexpRefList):
            self.log.info("Processing calexp %d of %d for this tempExp: id=%s",
                          calExpInd+1, len(calexpRefList), calExpRef.dataId)
            try:
                ccdId = calExpRef.get("ccdExposureId", immediate=True)
            except Exception:
                ccdId = calExpInd
            numGoodPix = 0
            try:
                # We augment the dataRef here with the tract, which is harmless for loading things
                # like calexps that don't need the tract, and necessary for meas_mosaic outputs,
                # which do.
                calExpRef = calExpRef.butlerSubset.butler.dataRef("calexp", dataId=calExpRef.dataId,
                                                                  tract=skyInfo.tractInfo.getId())
                calExp = self.getCalExp(calExpRef, bgSubtracted=self.config.bgSubtracted)
                exposure = self.warpAndPsfMatch.run(calExp, modelPsf=modelPsf, wcs=skyInfo.wcs,
                                                    maxBBox=skyInfo.bbox).exposure
                if didSetMetadata:
                    mimg = exposure.getMaskedImage()
                    mimg *= (coaddTempExp.getCalib().getFluxMag0()[0] / exposure.getCalib().getFluxMag0()[0])
                    del mimg
                numGoodPix = coaddUtils.copyGoodPixels(
                    coaddTempExp.getMaskedImage(), exposure.getMaskedImage(), self.getBadPixelMask())
                totGoodPix += numGoodPix
                self.log.debug("Calexp %s has %d good pixels in this patch (%.1f%%)",
                               calExpRef.dataId, numGoodPix, 100.0*numGoodPix/skyInfo.bbox.getArea())
                if numGoodPix > 0 and not didSetMetadata:
                    coaddTempExp.setCalib(exposure.getCalib())
                    coaddTempExp.setFilter(exposure.getFilter())
                    # PSF replaced with CoaddPsf after loop if and only if creating direct warp
                    coaddTempExp.setPsf(exposure.getPsf())
                    didSetMetadata = True
            except Exception as e:
                self.log.warn("Error processing calexp %s; skipping it: %s", calExpRef.dataId, e)
                continue
            inputRecorder.addCalExp(calExp, ccdId, numGoodPix)

        inputRecorder.finish(coaddTempExp, totGoodPix)
        if totGoodPix > 0 and didSetMetadata and not self.config.doPsfMatch:
            coaddTempExp.setPsf(CoaddPsf(inputRecorder.coaddInputs.ccds, skyInfo.wcs))

        self.log.info("coaddTempExp has %d good pixels (%.1f%%)",
                      totGoodPix, 100.0*totGoodPix/skyInfo.bbox.getArea())
        return coaddTempExp if totGoodPix > 0 and didSetMetadata else None
