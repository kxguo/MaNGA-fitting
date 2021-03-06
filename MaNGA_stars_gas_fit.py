import numpy as np
import matplotlib.pyplot as plt

import astropy.io.fits as fits
import astropy.table as table

from astropy import units as u, constants as c
import ppxf_util as util
from ppxf import ppxf
from scipy import ndimage
import manga_tools as m
import os
import pickle as pk

# for sampling
import emcee
import george
from george import kernels
import triangle

import misc_tools


def BPT(NII, SII, OI, OIII):
    '''
    BPT diagram in all three main styles

    Inputs are 2d arrays
    '''


def convolve_variable_width(a, sig, prec=1.):
    '''
    approximate convolution with a kernel that varies along the spectral
        direction, by stretching the data by the inverse of the kernel's
        width at a given position

    N.B.: this is an approximation to the proper operation, which
        involves iterating over each pixel of each template and
        performing ~10^6 convolution operations

    Parameters:
     - a: N-D array; convolution will occur along the final axis
     - sig: 1-D array (must have same length as the final axis of a);
        describes the varying width of the kernel
     - prec: precision argument. When higher, oversampling is more thorough
    '''

    assert (len(sig) == a.shape[-1]), '\tLast dimension of `a` must equal \
        length of `sig` (each element of a must have a convolution width)'

    sig0 = sig.max()  # the "base" width that results in minimal blurring
    # if prec = 1, just use sig0 as base.

    n = np.rint(prec * sig0/sig).astype(int)
    print n.min()
    # print n
    print '\tWarped array length: {}'.format(n.sum())
    # define "warped" array a_w with n[i] instances of a[:,:,i]
    a_w = np.repeat(a, n, axis=-1)
    # now a "warped" array sig_w
    sig_w = np.repeat(sig, n)

    # define start and endpoints for each value
    nl = np.cumsum(np.insert(n, 0, 0))[:-1]
    nr = np.cumsum(n)
    # now the middle of the interval
    nm = np.rint(np.median(np.column_stack((nl, nr)), axis=1)).astype(int)

    # print nm

    # print a_w.shape, sig_w.shape # check against n.sum()

    # now convolve the whole thing with a Gaussian of width sig0
    print '\tCONVOLVE...'
    # account for the increased precision required
    a_w_f = np.empty_like(a_w)
    # have to iterate over the rows and columns, to avoid MemoryError
    c = 0  # counter (don't judge me, it was early in the morning)
    for i in range(a_w_f.shape[0]):
        for j in range(a_w_f.shape[1]):
            '''c += 1
            print '\t\tComputing convolution {} of {}...'.format(
                c, a_w_f.shape[0] * a_w_f.shape[1])'''
            a_w_f[i, j, :] = ndimage.gaussian_filter1d(
                a_w[i, j, :], prec*sig0)
    # print a_w_f.shape # should be the same as the original shape

    # and downselect the pixels (take approximate middle of each slice)
    # f is a mask that will be built and applied to a_w_f

    # un-warp the newly-convolved array by selecting only the slices
    # in dimension -1 that are in nm
    a_f = a_w_f[:, :, nm]

    return a_f


def setup_MaNGA_stellar_libraries(fname_ifu, fname_tem, z=0.01,
                                  plot=False):
    '''
    set up all the required stellar libraries for a MaNGA datacube

    this should only need to be run once.
    '''

    print 'Reading drpall...'
    drpall = fits.open(m.drpall_loc + 'drpall-v1_3_3.fits')[0].data

    print 'Reading MaNGA HDU...'
    MaNGA_hdu = fits.open(fname_ifu)

    # open global MaNGA header
    glob_h = MaNGA_hdu[0].header

    print 'Constructing wavelength grid...'
    # read in some average value for wavelength solution and spectral res
    L_ifu = m.wave(MaNGA_hdu).data
    R_avg, l_avg, = m.res_over_plate('MPL-3', '7443', plot=plot)
    FWHM_avg = l_avg / R_avg  # FWHM of a galaxy in AA at some wavelength

    # now read in basic info about templates and
    # up-sample "real" spectral resolution to the model wavelength grid
    tems = fits.open(fname_tem)[0]
    htems = tems.header
    logL_tem = np.linspace(
        htems['CRVAL1'],
        htems['CRVAL1'] + (htems['NAXIS1'] - 1) * htems['CDELT1'],
        htems['NAXIS1'])  # base e
    L_tem = np.exp(logL_tem)

    dL_tem = np.empty_like(L_tem)
    dL_tem[:-1] = L_tem[1:] - L_tem[:-1]
    dL_tem[-1] = dL_tem[-2]  # this is not exact, but it's efficient

    # since the impulse-response of the templates is infinitely thin
    # approximate the FWHM as half the pixel width
    FWHM_tem = dL_tem/2.

    FWHM_avg_s = np.interp(x=L_tem, xp=l_avg, fp=FWHM_avg)

    if plot == True:
        plt.close('all')
        fig = plt.figure(figsize=(8, 6))
        ax = fig.add_subplot(111)
        for z in [0.00, 0.01, 0.02]:
            # get sigma for a bunch of different redshifts
            FWHM_diff_ = np.sqrt(
                (FWHM_avg_s / (1. + z))**2. - FWHM_tem**2.)
            sigma_ = FWHM_diff_/2.355/dL_tem
            ax.plot(L_tem, sigma_,
                    label='z = {:.3f}'.format(z))
        ax.legend(loc='best')
        ax.set_xlabel(r'$\lambda[\AA]$')
        ax.set_ylabel(r'$\frac{R_{tem}}{R_{spec}}$')
        plt.tight_layout()
        plt.show()

    logL_ifu = np.log(L_ifu)
    dlogL_ifu = np.log(10.**MaNGA_hdu[0].header['CD3_3'])

    velscale_ifu = np.asarray((dlogL_ifu * c.c).to(u.km/u.s))
    print 'velscale:', velscale_ifu

    print 'Constructing spectral library files...'

    #
    # file format is st-<REDSHIFT>.fits
    # <REDSHIFT> is of form 0.XXXX

    FWHM_diff = np.sqrt(
        (FWHM_avg_s / (1. + z))**2. - FWHM_tem**2.)
    sigma = FWHM_diff/2.355/dL_tem

    print tems.data.shape

    a_f = convolve_variable_width(tems.data, sigma, prec=4.)
    # return a_f, dlogL_ifu, logL_tem
    spec_ssp_new_sample, logL_ssp_new = m.ssp_rebin(
        logL_tem, a_f[0, 0, :], dlogL_ifu)
    spec_ssp_new = np.empty([a_f.shape[0], a_f.shape[1], len(logL_ssp_new)])

    for Ti in range(a_f.shape[0]):
        for Zi in range(a_f.shape[1]):
            spec_ssp_new[Ti, Zi, :] = m.ssp_rebin(
                logL_tem, a_f[Ti, Zi, :], dlogL_ifu)[0]

    fname2 = 'stellar_libraries/st-{0:.4f}.fits'.format(z)
    print '\tMaking HDU:', fname2

    blurred_hdu = fits.PrimaryHDU(spec_ssp_new)
    blurred_hdu.header = tems.header
    blurred_hdu.header['z'] = z
    blurred_hdu.header['NAXIS1'] = len(logL_ssp_new)
    blurred_hdu.header['CRVAL1'] = logL_ssp_new[0]
    blurred_hdu.header['CDELT1'] = dlogL_ifu
    blurred_hdu.writeto(fname2, clobber=True)


def kin_models(tems):
    '''
    Return those SSP models suitable for kinematic fitting. We're i
    in solar-metallicity models that best span the spectral shape-space
    '''

    htems = tems[0].header
    dtems = tems[0].data

    nT, nZ, nL = dtems.shape

    Zs = htems['CRVAL2'] + np.linspace(0.,
        htems['CDELT2'] * (htems['NAXIS2'] - 1), htems['NAXIS2'])
    Ts = htems['CRVAL3'] + np.linspace(0.,
        htems['CDELT3'] * (htems['NAXIS3'] - 1), htems['NAXIS3'])

    logL_tem = np.linspace(
        htems['CRVAL1'],
        htems['CRVAL1'] + (nL - 1) * htems['CDELT1'], nL)  # base e

    tems_sol = dtems[:, np.argmin(np.abs(Zs)), :]
    tems_red = tems_sol

def ppxf_run_MaNGA_galaxy(ifu, fname_tem, first_few=None, Tsample = 4,
                          over = True):

    plt.close('all')
    plate = ifu[0]
    ifudsgn = ifu[1]
    fname_ifu = 'manga-{}-{}-LOGCUBE.fits.gz'.format(plate, ifudsgn)

    # now read in drpall
    drpall = table.Table.read(m.drpall_loc + 'drpall-v1_3_3.fits',
                              format='fits')
    objconds = drpall['plateifu'] == '{}-{}'.format(plate, ifudsgn)
    obj = drpall[objconds]

    c = 299792.458
    z = obj['nsa_redshift']
    if z == -9999.:
        z = 0.1

    # now read in IFU
    ifu = fits.open(fname_ifu)
    ifu_flux = ifu['FLUX'].data
    ifu_ivar = ifu['ivar'].data
    ifu_mask = ifu['MASK'].data
    L_ifu = ifu['WAVE'].data
    red_scattered_light = L_ifu > 9500.
    logL_ifu = np.log(L_ifu) - z
    L_ifu = np.exp(logL_ifu)

    res_path = '{}-{}/'.format(plate, ifudsgn)
    try:
        os.makedirs(res_path)
    except OSError:
        pass

    # read in stellar templates
    # templates have already been convolved to the proper resolution
    tems = fits.open(fname_tem)
    htems = tems[0].header
    dtems = tems[0].data[::Tsample, :, :]

    dtems_red = kin_models(tems)
    print dtems_red.shape

    nT, nZ, nL = dtems.shape

    Zs = htems['CRVAL2'] + np.linspace(0.,
        htems['CDELT2'] * (htems['NAXIS2'] - 1), htems['NAXIS2'])
    Ts = htems['CRVAL3'] + np.linspace(0.,
        htems['CDELT3'] * (htems['NAXIS3'] - 1), htems['NAXIS3'])

    logL_tem = np.linspace(
        htems['CRVAL1'],
        htems['CRVAL1'] + (nL - 1) * htems['CDELT1'], nL)  # base e

    L_tem = np.exp(logL_tem)

    dtems_med = np.median(dtems)
    dtems /= dtems_med

    ssps = np.swapaxes(dtems, 0, 2)
    ssps = np.reshape(ssps, (nL, -1))

    ssps_red = np.swapaxes(dtems_red, 0, 2)
    ssps_red = np.reshape(ssps_red, (nL, -1))

    vel = z * c
    veldisp = obj['nsa_vdisp']
    if veldisp < 0.:  # deal with non-measured veldisps
        veldisp = 300.

    velscale = (logL_ifu[1] - logL_ifu[0])*c
    dl = L_tem[0] - L_ifu[0]
    dv = c * (logL_tem[0] - logL_ifu[0])  # km/s
    moments = 4
    regul_err = .01
    ebvgal = obj['ebvgal']
    start = [0., veldisp]
    reg_dim = [nZ, nT]

    spaxels = which_spaxels(fname_ifu)

    n_stellar_tems = nZ * nT
    moments0 = [2, ]
    moments1 = [-2, ]
    start = [0., veldisp]

    i = 0

    for spaxel in spaxels:

        gridx, gridy = spaxel['gridx'], spaxel['gridy']
        print 'Spaxel row {}; col {}'.format(gridx, gridy)
        figpath = res_path + '{}-{}_pp_fit.png'.format(gridx, gridy)
        fig_ex = os.path.exists(figpath)

        if (spaxel['good'] == True) and ((fig_ex == False) or (over == True)):
            goodpixels = 1 - (ifu_mask[:, gridy, gridx] & 10)
            goodpixels *= (1 - (ifu_mask[:, gridy, gridx] & 8))
            goodpixels *= (np.isfinite(ifu_ivar[:, gridy, gridx]))
            # red end has some scattered light, so throw it out
            goodpixels *= (1 - red_scattered_light)
            emlines = line_mask(logL_ifu, 800.)
            goodpixels *= emlines
            goodpixels_i = np.where(goodpixels)[0]

            galaxy = ifu_flux[:, gridy, gridx]
            ivar = ifu_ivar[:, gridy, gridx]
            noise = 1./np.sqrt(ivar)
            noise = np.where(np.isfinite(noise), noise, 9999.)
            med = np.median(galaxy)

            try:

                pp0 = ppxf(templates=ssps_red,
                           galaxy=galaxy/med,
                           noise=noise/med,
                           goodpixels=goodpixels_i, start=start, vsyst=dv,
                           velScale=velscale, moments=moments0, degree=-1,
                           mdegree=-1, clean=False, lam=L_ifu, regul=None)

                pp = ppxf(templates=ssps,
                          galaxy=galaxy/med,
                          noise=noise/med,
                          goodpixels=goodpixels_i, start=pp0.sol, vsyst=dv,
                          velScale=velscale, moments=moments1, degree=-1,
                          mdegree=-1, reddening=ebvgal,
                          clean=False, lam=L_ifu,
                          regul = 1./regul_err, reg_dim=reg_dim)

                #print('Desired Delta Chi^2: %.4g' % np.sqrt(2*galaxy.size))
                #print('Current Delta Chi^2: %.4g' % ((pp.chi2 - 1)*\
                #    galaxy.size))

            except:
                print '\tFITTING PROBLEM'
                fit_success = False

            else:
                ppxf_fig(pp, (gridx, gridy), tems, (plate, ifudsgn), reg_dim)
                plt.savefig(figpath, dpi=300)

                start = pp.sol[:2]

                pp_res = {'bestfit': pp.bestfit, 'chi2': pp.chi2,
                'error': pp0.error, 'galaxy': pp.galaxy, 'lam': pp.lam,
                'sol': pp0.sol, 'vsyst': pp.vsyst, 'noise': pp.noise,
                'specres': ifu['SPECRES']}

                pk.dump(pp_res, file(res_path + '{}-{}-pp.pickle'.format(
                    gridx, gridy), 'w'))

            finally:
                i += 1
                if i >= first_few:
                    break


def line_mask(logL_tem, dv=800.):
    # mask out Ha - H12, OIII, OII, NII, SIII, SII
    # these will really distrupt fitting
    balmer = rydberg_formula(np.ones(15).astype(int),
        np.arange(2, 17, 1).astype(int))

    metal_lines = []
    # OIII doublet
    metal_lines += [5008.240, 4960.295]

    # OII doublet
    metal_lines += [3727.092, 3729.875]

    # SII doublet
    metal_lines += [6718.294, 6732.674]

    # NII doublet
    metal_lines += [6547.35, 6584.42]

    # SIII
    metal_lines += [9068.6, 9530.6]

    lines = np.concatenate((balmer, metal_lines))
    lines_logL = np.log(lines)

    mask = np.ones(len(logL_tem))

    c = 299792.458
    logL_tol = dv/c

    for i, l in enumerate(lines_logL):
        mask *= (np.abs(logL_tem - l) >= dv/c)

    return mask


def rydberg_formula(n1, n2):
    R = 1.097e7
    l_vac = 1. / (R * (1./n1**2. - 1./n2**2.))
    return l_vac * 1.e10

def ppxf_fig(pp, spaxel, tems, galname, reg_dim):
    nT, nZ = tems[0].data.shape[:-1]

    Z0 = tems[0].header['CRVAL2']
    dZ = tems[0].header['CDELT2']

    LT0 = np.log10(np.exp(tems[0].header['CRVAL3']))
    dLT = tems[0].header['CDELT3'] / (np.log(10))

    Zrange = [Z0, Z0 + (nZ - 1) * dZ]
    logTrange = [LT0, LT0 + (nT - 1) * dLT]

    n_stellar_tems = np.array(reg_dim).prod()
    n_gas_tems = len(pp.matrix) - n_stellar_tems

    # print Zrange, logTrange

    galaxy = pp.galaxy
    noise = pp.noise

    NAXIS1 = len(galaxy)

    plt.close('all')

    fig = plt.figure(figsize=(8, 6), dpi=300)

    import matplotlib.gridspec as gridspec
    gs = gridspec.GridSpec(4, 1, height_ratios=[3, 1, 1.25, 2])
    ax1 = plt.subplot(gs[0])

    # first plot the input spectrum
    ax1.plot(pp.lam, pp.galaxy, c='k', linewidth=0.25, zorder=2,
             label='galaxy', drawstyle='steps-mid', alpha=0.5)
    ax1.fill_between(
        pp.lam,
        (galaxy - noise),
        (galaxy + noise), edgecolor='#ff0000',
        facecolor='coral', zorder=1, linewidth=0.5, alpha=0.75)

    # residuals
    mn = 0.*np.min(pp.bestfit[pp.goodpixels])
    mx = np.max(pp.bestfit[pp.goodpixels])
    resid = mn + pp.galaxy - pp.bestfit
    mn1 = np.min(resid[pp.goodpixels])
    ax1.plot(pp.lam[pp.goodpixels], resid[pp.goodpixels],
             marker='.', markersize=2, c='cyan',
             markeredgecolor='cyan', linestyle='None', zorder=1)
    ax1.plot(pp.lam[pp.goodpixels], pp.goodpixels*0 + mn,
             marker=',', c='k', zorder=0)

    w = np.where(np.diff(pp.goodpixels) > 1)[0]
    if w.size > 0:
        for wj in w:
            x = np.arange(pp.goodpixels[wj], pp.goodpixels[wj+1])
            ax1.plot(pp.lam[x], resid[x], 'indigo')
        w = np.hstack([0, w, w+1, -1])  # Add first and last point
    else:
        w = [0, -1]
    for gj in pp.goodpixels[w]:
        ax1.plot([pp.lam[gj], pp.lam[gj]], [mn, pp.bestfit[gj]],
                 color='orange', linewidth=0.5)

    # turn off tick labels for x axis
    ax1.spines['bottom'].set_visible(False)
    plt.setp(ax1.get_xticklabels(), visible=False)
    ax1.set_ylabel("Counts", fontsize=16)
    #ax1.legend(loc='best')

    # set up a twin axis to display pixel positions
    # DO NOT CHANGE XLIMS OF ANYTHING!!!!

    ax1_pix = ax1.twiny()
    ax1_pix.plot(np.arange(NAXIS1), np.zeros(NAXIS1))
    ax1_pix.set_xlim([0, NAXIS1 - 1])
    ax1_pix.set_xlabel('Pixel')

    ax1.set_ylim([max(mn1 - .05, -.25), mx + .05])

    # set up an axis to display residuals vs noise

    ax1_res = plt.subplot(gs[1], sharex=ax1)
    ax1_res.plot(pp.lam[pp.goodpixels],
                 (noise/galaxy)[pp.goodpixels], marker='.',
                 c='coral', linestyle='None', markersize=0.5,
                 label='noise', alpha = 0.5)
    ax1_res.plot(pp.lam[pp.goodpixels],
                 (resid/galaxy)[pp.goodpixels], marker='.',
                 c='cyan', linestyle='None', markersize=0.5,
                 label='resid', alpha=0.5)
    ax1_res.set_xlabel(r'$\lambda_r ~ [\AA]$')

    ax1_res.legend(loc='best', prop={'size': 6})
    ax1_res.set_ylabel(r'$\Delta_{rel}$')
    ax1_res.set_yscale('log')
    ax1_res.set_ylim([10**-2.5, 1.])

    _ = [tick.label.set_fontsize(8) for tick in
         ax1_res.yaxis.get_major_ticks()]

    ax1.set_xlim([np.min(pp.lam), np.max(pp.lam)])

    # this is just a dummy axis for spacing purposes
    pad_ax = plt.subplot(gs[2])
    pad_ax.axis('off')

    ax2 = plt.subplot(gs[3])

    # extract the kinematics of the stars first
    weights = np.reshape(pp.weights, reg_dim)
    weights /= weights.sum()
    # print weights

    plt.imshow(
        weights, origin='lower', interpolation='nearest',
        cmap='cubehelix_r',
        vmin=0.0, extent=(LT0 - dLT/2., logTrange[1] + dLT/2.,
                          Z0 - dZ/2., Zrange[1] + dZ/2.))

    cb = plt.colorbar()
    for t in cb.ax.get_yticklabels():
        t.set_fontsize(14)
    plt.title("Mass Fraction", size=16)
    plt.xlabel(r'$\log_{10} \tau ~ [\mathrm{Gyr}]$', size=16)
    plt.ylabel(r'$[M/H]$', size=16)

    t = r'{}-{} pPXF fit: spaxel ({}, {})'.format(galname[0], galname[1],
                                                 spaxel[0], spaxel[1])

    plt.suptitle(t, size=18)
    plt.subplots_adjust(hspace=0.01, top=0.85)
    # plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    return fig


def which_spaxels(fname_ifu):
    '''
    list of spaxel indices in the order that they should be run

    WILL NEED TO BE ALTERED FOR MPL-4, SINCE THE AT THAT POINT, THE
    NEEDED HEADER KEYWORDS WILL BE FIXED
    '''

    ifu = fits.open(fname_ifu)
    ifu_flux = ifu['FLUX'].data
    r_im = ifu['RIMG'].data

    # now figure out where in the ifu the center of the galaxy is
    # and use that info to figure out where in the galaxy to start

    NL, NX, NY = ifu_flux.shape
    # print NX
    # print pixpos_x.shape, pixpos_y.shape, r_im.shape

    pos_x = np.linspace(-0.5*NX/2., 0.5*NX/2., NX)
    pos_y = np.linspace(-0.5*NY/2., 0.5*NY/2., NY)
    pixpos_x, pixpos_y = np.meshgrid(pos_x, pos_y)

    peak_inds = np.unravel_index(np.argmax(r_im), pixpos_x.shape)
    # print peak_inds

    peak_distance = np.sqrt((pixpos_x - pos_x[peak_inds[1]])**2. +
                            (pixpos_y - pos_y[peak_inds[0]])**2.)

    grid = np.indices(peak_distance.shape)
    gridx, gridy = grid[1], grid[0]

    xnew = pixpos_x - peak_inds[1]
    ynew = pixpos_y - peak_inds[0]

    theta_new = np.arctan2(ynew, xnew)

    # peak_distance and theta_new are coords relative to ctr of galaxy

    # plt.imshow(r_im, origin='lower', aspect='equal')
    # plt.scatter(peak_inds[1], peak_inds[0])
    # plt.show()

    # now make a table out of everything, so that it can get sorted

    pixels = table.Table()
    pixels['good'] = m.good_spaxels(ifu).flatten()
    pixels['gridx'] = gridx.flatten()
    pixels['gridy'] = gridy.flatten()
    pixels['r'] = peak_distance.flatten()
    pixels['theta'] = theta_new.flatten()
    pixels.sort(['r', 'theta'])
    pixels['order'] = range(len(pixels))

    # print pixels

    '''plt.scatter(pixels['gridx'], pixels['gridy'],
                c=pixels['order']*pixels['good'], s=5)
    plt.colorbar()
    plt.show()'''

    return pixels

