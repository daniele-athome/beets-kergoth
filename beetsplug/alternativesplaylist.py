from __future__ import division, absolute_import, print_function

import fnmatch
import os

import beets
from beets.util import (bytestring_path, mkdirall, normpath, path_as_posix,
                        sanitize_path, syspath)
from beetsplug import playlist
from confuse import Filename


class AlternativesPlaylistPlugin(beets.plugins.BeetsPlugin):
    def __init__(self):
        super(AlternativesPlaylistPlugin, self).__init__()
        self.config.add({
            'auto': False,
            'playlist_dir': '_Playlists',
            'relative_to': 'playlist',
            'is_relative': True,
        })

        # FIXME: make 'library' go based on the alt root, not actual lib
        if self.config['relative_to'].get() == 'library':
            self.relative_to = beets.util.bytestring_path(
                beets.config['directory'].as_filename())
        elif self.config['relative_to'].get() != 'playlist':
            self.relative_to = beets.util.bytestring_path(
                self.config['relative_to'].as_filename())
        else:
            self.relative_to = None
        self.is_relative = self.config['is_relative'].get()

        self.alternatives, self.playlist = None, None

        self.register_listener('pluginload', self.pluginload)
        if self.config['auto'].get():
            self.register_listener(
                'alternatives_after_update_with_lib', self.alternatives_after_update)

    def commands(self):
        spl_alt_update = beets.ui.Subcommand(
            'altplaylistupdate',
            help=u'update the playlists for alternatives. Playlist names may be '
            u'passed as arguments. This assumes the alt itself has already been updated.'
        )
        spl_alt_update.func = self.update_cmd
        return [spl_alt_update]

    def update_cmd(self, lib, opts, args):
        if not args:
            raise beets.ui.UserError('must specify alternative')
        args = beets.ui.decargs(args)
        alternative = args.pop(0)
        self.alternatives_after_update(alternative, lib)

    def pluginload(self):
        for plugin in beets.plugins.find_plugins():
            if plugin.name == 'alternatives':
                self.alternatives = plugin
                self.alternatives.update = self.patch_alt_update
            elif plugin.name == 'playlist':
                self.playlist = plugin

        if not self.alternatives:
            raise beets.ui.UserError('alternatives plugin is required for alternativesplaylist')
        if not self.playlist:
            raise beets.ui.UserError('playlist plugin is required for alternativesplaylist')

    def alternatives_after_update(self, alternative, lib):
        for m3u in self.find_playlists():
            try:
                self.update_playlist(lib, alternative.name, m3u)
            except beets.util.FilesystemError:
                self._log.error('Failed to update playlist: {0}'.format(
                    beets.util.displayable_path(playlist)))

    def find_playlists(self):
        playlist_dir = beets.util.syspath(self.playlist.playlist_dir)
        for file in find(playlist_dir):
            if fnmatch.fnmatch(file, '*.[mM]3[uU]') or fnmatch.fnmatch(file, '*.[mM]3[uU]8'):
                m3u = beets.util.bytestring_path(file)
                yield m3u

    def update_playlist(self, lib, alternative, m3u):
        playlist_dir = beets.util.bytestring_path(self.playlist.playlist_dir)
        m3uname = os.path.relpath(m3u, playlist_dir)

        outm3u = os.path.join(self.playlist_dir(alternative), m3uname)
        self._log.info('Writing playlist {}'.format(beets.util.displayable_path(outm3u)))

        # Gather up the items in the playlist and map to the alternative
        m3ubase, _ = os.path.splitext(m3uname)
        query = playlist.PlaylistQuery(beets.util.as_string(m3ubase))
        pathmap = {}
        for item in lib.items(query):
            pathmap[beets.util.bytestring_path(item.path)] = beets.util.bytestring_path(item.get(u'alt.{}'.format(alternative)) or u'')

        src_base_dir = beets.util.bytestring_path(
            self.playlist.relative_to if self.playlist.relative_to
            else os.path.dirname(m3u)
        )
        alt_base_dir = beets.util.bytestring_path(
            self.relative_to if self.relative_to
            else os.path.dirname(outm3u)
        )

        lines = []
        with open(m3u, mode='rb') as m3ufile:
            for line in m3ufile:
                srcpath = line.rstrip(b'\r\n')
                is_relative = not os.path.isabs(srcpath)
                if is_relative:
                    srcpath = os.path.join(src_base_dir, srcpath)
                srcpath = beets.util.normpath(srcpath)

                newpath = pathmap.get(srcpath)
                if not newpath:
                    self._log.error('Failed to map path {} in playlist {} for alt {}', srcpath, m3uname, alternative)
                    return

                if is_relative or self.is_relative:
                    newpath = os.path.relpath(newpath, alt_base_dir)

                lines.append(line.replace(srcpath, newpath))

        mkdirall(outm3u)
        with open(outm3u, 'wb') as m3ufile:
            m3ufile.writelines(lines)

    def playlist_dir(self, alternative):
        alt_dir = self.alternatives.config[alternative]['directory'].as_filename()
        playlist_dir = self.config['playlist_dir'].get(Filename(cwd=alt_dir))
        playlist_dir = bytestring_path(playlist_dir)
        return playlist_dir

    def patch_alt_update(self, lib, options):
        """Tweaked update method for alternatives to send events with the lib object."""
        try:
            alt = self.alternatives.alternative(options.name, lib)
        except KeyError as e:
            raise beets.ui.UserError(u"Alternative collection '{0}' not found."
                                     .format(e.args[0]))
        beets.plugins.send('alternatives_before_update', alternative=alt)
        beets.plugins.send(
            'alternatives_before_update_with_lib', alternative=alt, lib=lib)
        alt.update(create=options.create)
        beets.plugins.send('alternatives_after_update_with_lib',
                           alternative=alt, lib=lib)
        beets.plugins.send('alternatives_after_update', alternative=alt)


def find(dir, dirfilter=None, **walkoptions):
    """ Given a directory, recurse into that directory,
    returning all files as absolute paths. """

    for root, dirs, files in os.walk(dir, **walkoptions):
        if dirfilter is not None:
            for d in dirs:
                if not dirfilter(d):
                    dirs.remove(d)

        for file in files:
            yield os.path.join(root, file)
